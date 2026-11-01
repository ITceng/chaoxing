from __future__ import annotations

import click
from pathlib import Path
from flask import Flask, jsonify, send_from_directory
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError, TimeoutError as SQLAlchemyTimeoutError
from werkzeug.middleware.proxy_fix import ProxyFix

from unified_system.api.admin import bp as admin_bp
from unified_system.api.auth import bp as auth_bp
from unified_system.api.catalog import bp as catalog_bp
from unified_system.api.accounts import bp as accounts_bp
from unified_system.api.activity import bp as activity_bp
from unified_system.api.courses import bp as courses_bp
from unified_system.api.shared_admin import bp as shared_admin_bp
from unified_system.api.tasks import bp as tasks_bp
from unified_system.api.worker import bp as worker_bp
from unified_system.bootstrap import seed_catalog
from unified_system.config import settings_from_env
from unified_system.db import Base, close_db, get_db, init_database
from unified_system.models import AIProviderConfig, PlatformAccountSecret, Role, User, UserRole
from unified_system.security import (
    decrypt_secret, encrypted_with_active_key, hash_password, validate_credential_keyring,
    credential_keyring, encrypt_secret,
)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.update(settings_from_env())
    if test_config:
        app.config.update(test_config)
    proxy_hops = int(app.config.get("TRUSTED_PROXY_HOPS", 0))
    if not 0 <= proxy_hops <= 2:
        raise RuntimeError("TRUSTED_PROXY_HOPS must be between 0 and 2")
    if proxy_hops:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_hops,
            x_proto=proxy_hops,
            x_host=proxy_hops,
        )
    app.config.update(
        SESSION_COOKIE_NAME="unified_learning_session",
        SESSION_COOKIE_SECURE=app.config["COOKIE_SECURE"],
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        JSON_AS_ASCII=False,
    )
    if app.config.get("APP_ENV") == "production":
        validate_credential_keyring(app.config)

    init_database(app)
    app.teardown_appcontext(close_db)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(activity_bp)
    app.register_blueprint(courses_bp)
    app.register_blueprint(shared_admin_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(worker_bp)

    frontend_dist = Path(__file__).resolve().parent / "frontend" / "dist"

    @app.get("/")
    def index():
        built_index = frontend_dist / "index.html"
        if built_index.is_file():
            return send_from_directory(str(frontend_dist), "index.html")
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/<path:frontend_path>")
    def frontend_route(frontend_path: str):
        if frontend_path.startswith("api/"):
            return jsonify({"error": {"message": "Not found", "status": 404}}), 404
        if not (frontend_dist / "index.html").is_file():
            return jsonify({"error": {"message": "Not found", "status": 404}}), 404
        candidate = (frontend_dist / frontend_path).resolve()
        if candidate.is_file() and frontend_dist.resolve() in candidate.parents:
            return send_from_directory(str(frontend_dist), frontend_path)
        return send_from_directory(str(frontend_dist), "index.html")

    @app.get("/api/v1/health")
    def health():
        return jsonify({"status": "ok", "service": "unified-learning-api", "version": "0.1.0"})

    @app.get("/api/v1/health/ready")
    def readiness():
        try:
            get_db().execute(text("SELECT 1"))
        except SQLAlchemyError:
            get_db().rollback()
            app.logger.warning("readiness check failed: database unavailable")
            return jsonify({"status": "not_ready", "checks": {"database": "unavailable"}}), 503
        return jsonify({"status": "ready", "checks": {"database": "ok"}})

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; form-action 'self'; "
            "script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; font-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        # HSTS belongs at the verified HTTPS termination point, not the HTTP-capable API process.
        return response

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify({"error": {"message": "Request body too large", "status": 413}}), 413

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        unavailable = isinstance(error, (OperationalError, SQLAlchemyTimeoutError)) or bool(
            getattr(error, "connection_invalidated", False)
        )
        app.logger.error("database operation failed (%s)", type(error).__name__)
        status = 503 if unavailable else 500
        message = "Database temporarily unavailable" if unavailable else "Database operation failed"
        return jsonify({"error": {"message": message, "status": status}}), status

    @app.cli.command("seed-catalog")
    def seed_catalog_command():
        """Create or update the platform/role/permission catalog after Alembic migrations."""
        with app.app_context():
            seed_catalog()
        click.echo("System role, permission, and platform catalog seeded.")

    @app.cli.command("init-db")
    def init_db_command():
        """Create the initial schema for local development and seed system catalog."""
        if app.config.get("APP_ENV") == "production":
            raise click.ClickException("init-db is disabled in production; use Alembic migrations.")
        Base.metadata.create_all(app.extensions["unified_engine"])
        with app.app_context():
            seed_catalog()
        click.echo("Database schema and platform catalog initialized.")

    @app.cli.command("rotate-credential-encryption-key")
    @click.option("--apply", "apply_changes", is_flag=True, help="Re-encrypt all stored platform and AI credentials transactionally.")
    def rotate_credential_encryption_key_command(apply_changes: bool):
        """Dry-run by default; re-encrypt stored secrets under the configured active key ID."""
        with app.app_context():
            _keyring, active_id = credential_keyring()
            if apply_changes:
                confirmation = click.prompt(
                    f"Type ROTATE:{active_id} to commit this key rotation", type=str,
                )
                if confirmation != f"ROTATE:{active_id}":
                    raise click.ClickException("Key rotation cancelled; no rows were changed.")
            db = get_db()
            updated = 0
            try:
                for model, attribute in (
                    (PlatformAccountSecret, "ciphertext"),
                    (AIProviderConfig, "secret_ciphertext"),
                ):
                    for row in db.scalars(select(model).with_for_update()).all():
                        value = getattr(row, attribute)
                        if not value or encrypted_with_active_key(value):
                            continue
                        plaintext = decrypt_secret(value)
                        setattr(row, attribute, encrypt_secret(plaintext))
                        updated += 1
                if apply_changes:
                    db.commit()
                else:
                    db.rollback()
            except click.ClickException:
                raise
            except Exception as exc:
                db.rollback()
                # Never include ciphertext, plaintext or database error text in the CLI output.
                raise click.ClickException(f"Key rotation aborted before commit ({type(exc).__name__}); no rows were changed.") from None
        state = "committed" if apply_changes else "dry-run; no changes written"
        click.echo(f"Credential key rotation {state}: rows_to_reencrypt={updated}, active_key_id={active_id}")

    @app.cli.command("bootstrap-admin")
    def bootstrap_admin_command():
        """Create the first superadmin from BOOTSTRAP_ADMIN_EMAIL/PASSWORD."""
        email = app.config.get("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        password = app.config.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        if not email or not password:
            raise click.ClickException("Set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD in the environment.")
        if len(password) < 12:
            raise click.ClickException("Bootstrap password must be at least 12 characters.")
        with app.app_context():
            db = get_db()
            if db.scalar(select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).where(Role.name == "superadmin")):
                raise click.ClickException("A superadmin already exists; use the audited reset procedure.")
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, display_name="System Administrator", password_hash=hash_password(password))
                db.add(user)
                db.flush()
            role = db.scalar(select(Role).where(Role.name == "superadmin"))
            if role is None:
                raise click.ClickException("Run init-db first.")
            db.add(UserRole(user_id=user.id, role_id=role.id))
            db.commit()
        click.echo("Initial superadmin created. Remove bootstrap credentials from the environment now.")

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8088, debug=False)
