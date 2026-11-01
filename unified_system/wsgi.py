"""Production WSGI entrypoint for Gunicorn."""

from unified_system.app import create_app

app = create_app()
