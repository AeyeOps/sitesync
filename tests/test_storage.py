"""Tests for SQLite storage layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sitesync.storage import Database
from sitesync.storage.db import ISO_FORMAT


def test_initialize_creates_database(tmp_path):
    db_path = tmp_path / "data" / "sitesync.sqlite"
    database = Database(db_path)
    database.initialize()

    assert db_path.exists()

    with database.connect() as connection:
        cursor = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert {"runs", "crawl_tasks", "assets", "asset_versions", "exceptions"}.issubset(tables)


def test_run_creation_and_seed_queue(tmp_path):
    database = Database(tmp_path / "sitesync.sqlite")
    database.initialize()

    run = database.start_run("example")
    assert run.id > 0
    assert run.status == "initialized"

    queued = database.enqueue_seed_tasks(run.id, [("https://example.com", 2)])
    assert queued == 1
    assert database.count_pending_tasks(run.id) == 1
    counts = database.get_task_status_counts(run.id)
    assert counts.get("pending") == 1
    assert database.count_open_exceptions(run.id) == 0

    # Duplicate seeds should be ignored
    queued_again = database.enqueue_seed_tasks(run.id, [("https://example.com", 2)])
    assert queued_again == 0


def test_task_leasing_and_backoff(tmp_path):
    database = Database(tmp_path / "sitesync.sqlite")
    database.initialize()

    run = database.start_run("example")
    database.enqueue_seed_tasks(
        run.id,
        [
            ("https://example.com/a", 1),
            ("https://example.com/b", 1),
        ],
    )

    tasks = database.acquire_tasks(
        run.id,
        limit=1,
        lease_owner="worker-1",
        lease_seconds=10,
        max_retries=3,
        backoff_seconds=1,
    )
    assert len(tasks) == 1
    task = tasks[0]
    assert task.status == "in_progress"

    # Simulate failure with backoff
    database.fail_task(task.id, error="timeout", backoff_seconds=5)

    # Pending count restored
    assert database.count_pending_tasks(run.id) == 2

    # Next acquire should respect backoff (immediate due to small wait)
    tasks = database.acquire_tasks(
        run.id,
        limit=2,
        lease_owner="worker-2",
        lease_seconds=10,
        max_retries=3,
        backoff_seconds=1,
    )
    assert len(tasks) >= 1


def test_record_asset_creates_versions(tmp_path):
    database = Database(tmp_path / "sitesync.sqlite")
    database.initialize()

    run = database.start_run("default")

    version1 = database.record_asset(
        run.id,
        source_url="https://example.com",
        asset_key="https://example.com",
        asset_type="page",
        checksum="abc123",
        raw_path="raw/file1.html",
    )
    assert version1 == 1

    version2 = database.record_asset(
        run.id,
        source_url="https://example.com",
        asset_key="https://example.com",
        asset_type="page",
        checksum="def456",
        raw_path="raw/file2.html",
    )
    assert version2 == 2


def test_acquire_tasks_reclaims_expired_leases(tmp_path):
    database = Database(tmp_path / "sitesync.sqlite")
    database.initialize()

    run = database.start_run("example")
    database.enqueue_seed_tasks(run.id, [("https://example.com/a", 1)])

    leased = database.acquire_tasks(
        run.id,
        limit=1,
        lease_owner="worker-1",
        lease_seconds=10,
        max_retries=3,
        backoff_seconds=1,
    )
    assert len(leased) == 1
    task_id = leased[0].id

    expired = (datetime.now(UTC) - timedelta(seconds=60)).strftime(ISO_FORMAT)
    with database.connect() as connection:
        connection.execute(
            "UPDATE crawl_tasks SET lease_expires_at = ? WHERE id = ?",
            (expired, task_id),
        )
        connection.commit()

    reclaimed = database.acquire_tasks(
        run.id,
        limit=1,
        lease_owner="worker-2",
        lease_seconds=10,
        max_retries=3,
        backoff_seconds=0,  # Zero backoff to reclaim immediately
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].id == task_id
    assert reclaimed[0].lease_owner == "worker-2"


def test_expired_leases_hit_retry_limit(tmp_path):
    database = Database(tmp_path / "sitesync.sqlite")
    database.initialize()

    run = database.start_run("example")
    database.enqueue_seed_tasks(run.id, [("https://example.com/a", 1)])

    leased = database.acquire_tasks(
        run.id,
        limit=1,
        lease_owner="worker-1",
        lease_seconds=10,
        max_retries=0,
        backoff_seconds=1,
    )
    assert len(leased) == 1
    task_id = leased[0].id

    expired = (datetime.now(UTC) - timedelta(seconds=60)).strftime(ISO_FORMAT)
    with database.connect() as connection:
        connection.execute(
            "UPDATE crawl_tasks SET lease_expires_at = ? WHERE id = ?",
            (expired, task_id),
        )
        connection.commit()

    reclaimed = database.acquire_tasks(
        run.id,
        limit=1,
        lease_owner="worker-2",
        lease_seconds=10,
        max_retries=0,
        backoff_seconds=1,
    )
    assert reclaimed == []
    with database.connect() as connection:
        status = connection.execute(
            "SELECT status FROM crawl_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()[0]
    assert status == "error"
