from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from unified_system.db import get_db
from unified_system.models import AccountExecutionLease
from unified_system.tests.test_accounts_workers import (
    activate_for_worker,
    create_shared,
    grant,
    post,
    sign_in,
)
from unified_system.tests.test_isolation import create_account


pytestmark = pytest.mark.skipif(
    not os.environ.get("UNIFIED_TEST_POSTGRESQL_URL"),
    reason="requires an explicitly disposable PostgreSQL test database",
)


def test_postgresql_for_update_skip_locked_and_waiting_lock(app):
    engine = app.extensions["unified_engine"]
    table = f"phase6_lock_probe_{uuid4().hex}"
    quoted = f'"{table}"'
    with engine.begin() as connection:
        connection.execute(text(f"CREATE TABLE {quoted} (id integer PRIMARY KEY, value integer NOT NULL)"))
        connection.execute(text(f"INSERT INTO {quoted} (id, value) VALUES (1, 10)"))

    locked = Event()
    release = Event()
    skipped_started = Event()
    waiting_started = Event()
    waiting_finished = Event()

    def hold_row_lock():
        with Session(engine) as session:
            session.begin()
            session.execute(text(f"SELECT id FROM {quoted} WHERE id = 1 FOR UPDATE")).first()
            locked.set()
            if not release.wait(10):
                raise TimeoutError("lock test release was not signalled")
            session.commit()

    def skip_locked_row():
        if not locked.wait(10):
            raise TimeoutError("lock holder did not acquire the row")
        skipped_started.set()
        with Session(engine) as session:
            session.begin()
            row = session.execute(text(f"SELECT id FROM {quoted} WHERE id = 1 FOR UPDATE SKIP LOCKED")).first()
            session.rollback()
            return row

    def wait_for_row_lock():
        if not locked.wait(10):
            raise TimeoutError("lock holder did not acquire the row")
        waiting_started.set()
        try:
            with Session(engine) as session:
                session.begin()
                row = session.execute(text(f"SELECT id FROM {quoted} WHERE id = 1 FOR UPDATE")).first()
                session.commit()
                return row
        finally:
            waiting_finished.set()

    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            holder = executor.submit(hold_row_lock)
            assert locked.wait(10)
            skipped = executor.submit(skip_locked_row)
            assert skipped_started.wait(10)
            assert skipped.result(timeout=5) is None
            waiting = executor.submit(wait_for_row_lock)
            assert waiting_started.wait(10)
            assert not waiting_finished.wait(timeout=0.25), "FOR UPDATE should wait until the owning transaction releases its lock"
            release.set()
            holder.result(timeout=10)
            assert waiting.result(timeout=10) == (1,)
    finally:
        release.set()
        with engine.begin() as connection:
            connection.execute(text(f"DROP TABLE IF EXISTS {quoted}"))


def test_postgresql_concurrent_task_creation_and_worker_claims_keep_one_account_slot(client, app):
    admin_csrf = sign_in(client)
    user_id = create_account(app, "postgres-worker-user@example.test")
    account_id = create_shared(client, admin_csrf)
    assert grant(client, admin_csrf, account_id, user_id).status_code == 201
    activate_for_worker(app, account_id)

    signed_in_clients = []
    for _ in range(4):
        user_client = app.test_client()
        csrf = sign_in(user_client, "postgres-worker-user@example.test", "valid-password-123")
        signed_in_clients.append((user_client, csrf))

    def create_task(index):
        user_client, csrf = signed_in_clients[index]
        return post(user_client, "/api/v1/tasks", {
            "account_mode": "shared",
            "account_id": account_id,
            "kind": "course_sync",
            "request_data": {"course_id": f"pg-course-{index}"},
            "idempotency_key": f"pg-concurrent-{index}",
        }, csrf)

    with ThreadPoolExecutor(max_workers=4) as executor:
        created = list(executor.map(create_task, range(4)))
    assert all(response.status_code == 202 for response in created), [response.get_json() for response in created]
    assert len({response.get_json()["task"]["id"] for response in created}) == 4

    token = "postgres-worker-integration-token-not-production"
    app.config["WORKER_TOKENS"] = {"chaoxing": token}
    worker_clients = []
    for worker_id in ("pg-worker-a", "pg-worker-b"):
        headers = {
            "X-Worker-Platform": "chaoxing",
            "X-Worker-ID": worker_id,
            "X-Worker-Token": token,
        }
        worker_client = app.test_client()
        heartbeat = worker_client.post(
            "/api/internal/workers/heartbeat",
            json={"version": "pg-integration", "capabilities": ["course_sync"]},
            headers=headers,
        )
        assert heartbeat.status_code == 200
        worker_clients.append((worker_client, headers))

    def claim(item):
        worker_client, headers = item
        return worker_client.post("/api/internal/workers/claim", json={}, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, worker_clients))
    assert sorted(response.status_code for response in claims) == [200, 204]

    with app.app_context():
        db = get_db()
        active_leases = db.scalars(select(AccountExecutionLease).where(
            AccountExecutionLease.account_id == account_id,
            AccountExecutionLease.task_id.is_not(None),
        )).all()
        assert len(active_leases) == 1
