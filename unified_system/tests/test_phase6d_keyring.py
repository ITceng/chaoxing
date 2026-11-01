from __future__ import annotations

import json

from cryptography.fernet import Fernet
from sqlalchemy import select

from unified_system.db import get_db
from unified_system.models import AIProviderConfig
from unified_system.security import decrypt_secret, encrypt_secret, encrypted_with_active_key


def configure_rotation_keyring(app):
    old_key = Fernet.generate_key().decode("ascii")
    new_key = Fernet.generate_key().decode("ascii")
    app.config.update(
        CREDENTIAL_ENCRYPTION_KEY=old_key,
        CREDENTIAL_ENCRYPTION_KEYS_JSON=json.dumps({"legacy": old_key, "current_2026": new_key}),
        CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID="current_2026",
    )
    return old_key, new_key


def test_new_secret_is_tagged_with_active_key_and_old_unversioned_ciphertext_still_decrypts(app):
    old_key, _new_key = configure_rotation_keyring(app)
    legacy_ciphertext = Fernet(old_key.encode("ascii")).encrypt(b"synthetic-test-secret").decode("ascii")
    with app.app_context():
        assert decrypt_secret(legacy_ciphertext) == "synthetic-test-secret"
        ciphertext = encrypt_secret("new-synthetic-secret")
        assert ciphertext.startswith("v1:current_2026:")
        assert encrypted_with_active_key(ciphertext)
        assert decrypt_secret(ciphertext) == "new-synthetic-secret"
        assert not encrypted_with_active_key(legacy_ciphertext)


def test_versioned_ciphertext_requires_a_known_key_id(app):
    configure_rotation_keyring(app)
    with app.app_context():
        try:
            decrypt_secret("v1:missing_key:invalid-token")
        except ValueError as exc:
            assert "versioned keyring" in str(exc)
        else:
            raise AssertionError("unknown credential key IDs must fail closed")


def test_production_keyring_requires_valid_active_key_id():
    from unified_system.security import validate_credential_keyring

    key = Fernet.generate_key().decode("ascii")
    try:
        validate_credential_keyring({
            "APP_ENV": "production",
            "CREDENTIAL_ENCRYPTION_KEY": "",
            "CREDENTIAL_ENCRYPTION_KEYS_JSON": json.dumps({"key_a": key}),
            "CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID": "missing",
        })
    except RuntimeError as exc:
        assert "not present" in str(exc)
    else:
        raise AssertionError("an invalid active key ID must fail startup validation")


def test_rotation_cli_dry_run_and_apply_reencrypt_only_database_values(app):
    old_key, _new_key = configure_rotation_keyring(app)
    legacy_ciphertext = Fernet(old_key.encode("ascii")).encrypt(b"synthetic-provider-test-token").decode("ascii")
    with app.app_context():
        db = get_db()
        db.add(AIProviderConfig(
            owner_user_id=None,
            provider="synthetic-test-provider",
            endpoint="https://provider.invalid/v1",
            model="test-only",
            secret_ciphertext=legacy_ciphertext,
            enabled=False,
        ))
        db.commit()

    runner = app.test_cli_runner()
    dry = runner.invoke(args=["rotate-credential-encryption-key"])
    assert dry.exit_code == 0, dry.output
    assert "rows_to_reencrypt=1" in dry.output
    assert "dry-run" in dry.output
    with app.app_context():
        db = get_db()
        row = db.scalar(select(AIProviderConfig).where(AIProviderConfig.provider == "synthetic-test-provider"))
        assert row.secret_ciphertext == legacy_ciphertext

    applied = runner.invoke(
        args=["rotate-credential-encryption-key", "--apply"],
        input="ROTATE:current_2026\n",
    )
    assert applied.exit_code == 0, applied.output
    assert "committed" in applied.output
    with app.app_context():
        db = get_db()
        row = db.scalar(select(AIProviderConfig).where(AIProviderConfig.provider == "synthetic-test-provider"))
        assert row.secret_ciphertext.startswith("v1:current_2026:")
        assert decrypt_secret(row.secret_ciphertext) == "synthetic-provider-test-token"
