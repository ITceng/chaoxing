# Database migrations

`0001_initial.py` is a frozen, explicit Alembic baseline generated from the reviewed phase-two schema. It is intended for an empty database. Once this baseline is adopted outside development, do not edit it; all future changes need a new revision with explicit operations.

Run migrations with `DATABASE_URL` injected from the deployment secret manager:

```sh
alembic -c unified_system/alembic.ini upgrade head
```

`flask init-db` is a local development convenience only and should not manage an existing database. The PostgreSQL SQL snapshot is generated from ORM metadata for review; Alembic remains the migration source of truth.

Validation performed in this stage: fresh SQLite `upgrade head`, `downgrade base`, then `upgrade head` all passed. A real PostgreSQL migration/concurrency test remains a deployment-stage requirement.
