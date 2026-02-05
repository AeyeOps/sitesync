"""SQLite persistence layer for Sitesync."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utcnow() -> str:
    """Return the current UTC timestamp in ISO format."""

    return datetime.now(timezone.utc).strftime(ISO_FORMAT)


@dataclass(slots=True)
class RunRecord:
    """Representation of a crawl run."""

    id: int
    source: str
    status: str
    started_at: str
    completed_at: Optional[str]
    label: Optional[str]


class Database:
    """SQLite-backed storage for Sitesync state."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = (path or Path.cwd() / "sitesync.sqlite").resolve()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Provide a SQLite connection with foreign keys enabled."""

        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create tables if they do not exist."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            cursor = connection.cursor()
            cursor.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    label TEXT
                );

                CREATE TABLE IF NOT EXISTS crawl_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    depth INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority INTEGER NOT NULL DEFAULT 0,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    next_run_at TEXT NOT NULL DEFAULT (DATETIME('now')),
                    created_at TEXT NOT NULL DEFAULT (DATETIME('now')),
                    updated_at TEXT NOT NULL DEFAULT (DATETIME('now')),
                    task_type TEXT NOT NULL DEFAULT 'page',
                    UNIQUE(run_id, url),
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    source_url TEXT NOT NULL,
                    asset_key TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checksum TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, asset_key),
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS asset_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    raw_path TEXT,
                    normalized_path TEXT,
                    metadata_json TEXT,
                    UNIQUE(asset_id, version),
                    FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS exceptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    url TEXT,
                    asset_key TEXT,
                    message TEXT NOT NULL,
                    context_json TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_crawl_tasks_status ON crawl_tasks(status);
                CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(asset_type);
                CREATE INDEX IF NOT EXISTS idx_assets_run_id ON assets(run_id);
                CREATE INDEX IF NOT EXISTS idx_exceptions_status ON exceptions(status);
                """
            )
            # Schema migration: add task_type if missing (for pre-0.6.0 databases)
            cursor.execute("PRAGMA table_info(crawl_tasks)")
            columns = {row[1] for row in cursor.fetchall()}
            if "task_type" not in columns:
                cursor.execute(
                    "ALTER TABLE crawl_tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'page'"
                )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_crawl_tasks_task_type ON crawl_tasks(task_type)"
            )
            connection.commit()

    def start_run(self, source: str, label: Optional[str] = None) -> RunRecord:
        """Create a new run row and return it."""

        started_at = _utcnow()
        with self.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO runs (source, status, started_at, label) VALUES (?, ?, ?, ?)",
                (source, "initialized", started_at, label),
            )
            run_id = cursor.lastrowid
            connection.commit()
        return RunRecord(
            id=run_id,
            source=source,
            status="initialized",
            started_at=started_at,
            completed_at=None,
            label=label,
        )

    def resume_run(self, source: str) -> Optional[RunRecord]:
        """Return the most recent non-completed run for a source, if any."""

        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT id, source, status, started_at, completed_at, label
                FROM runs
                WHERE source = ? AND status IN ('initialized', 'running', 'stopped')
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (source,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
        return RunRecord(
            id=row["id"],
            source=row["source"],
            status=row["status"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            label=row["label"],
        )

    def mark_run_status(self, run_id: int, status: str, *, completed: bool = False) -> None:
        """Update run status and timestamps."""

        values = [status]
        sql = "UPDATE runs SET status = ?"
        if completed:
            sql += ", completed_at = ?"
            values.append(_utcnow())
        sql += " WHERE id = ?"
        values.append(run_id)

        with self.connect() as connection:
            connection.execute(sql, values)
            connection.commit()

    def enqueue_seed_tasks(
        self, run_id: int, seeds: Iterable[tuple[str, int]], *, task_type: str = "page"
    ) -> int:
        """Insert seed tasks for a run. Returns number of new tasks queued."""

        timestamp = _utcnow()
        rows = [
            (run_id, url, depth, task_type, timestamp, timestamp, timestamp)
            for url, depth in seeds
        ]
        if not rows:
            return 0

        with self.connect() as connection:
            cursor = connection.executemany(
                """
                INSERT OR IGNORE INTO crawl_tasks (
                    run_id, url, depth, task_type, created_at, updated_at, next_run_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
            return cursor.rowcount

    def list_recent_runs(self, limit: int = 5, source: Optional[str] = None) -> list[RunRecord]:
        """Return recent runs ordered by start time descending."""

        sql = """
            SELECT id, source, status, started_at, completed_at, label
            FROM runs
        """
        params: list[Any] = []
        if source:
            sql += " WHERE source = ?"
            params.append(source)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        with self.connect() as connection:
            cursor = connection.execute(sql, params)
            rows = cursor.fetchall()

        return [
            RunRecord(
                id=row["id"],
                source=row["source"],
                status=row["status"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                label=row["label"],
            )
            for row in rows
        ]

    def get_run(self, run_id: int) -> RunRecord:
        """Return a single run record by id."""

        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT id, source, status, started_at, completed_at, label
                FROM runs
                WHERE id = ?
                """,
                (run_id,),
            )
            row = cursor.fetchone()

        if row is None:
            raise ValueError(f"Run {run_id} not found.")

        return RunRecord(
            id=row["id"],
            source=row["source"],
            status=row["status"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            label=row["label"],
        )

    def count_pending_tasks(self, run_id: int) -> int:
        """Return the number of pending tasks for a run."""

        with self.connect() as connection:
            cursor = connection.execute(
                "SELECT COUNT(*) FROM crawl_tasks WHERE run_id = ? AND status = 'pending'",
                (run_id,),
            )
            value = cursor.fetchone()[0]
        return int(value)

    def count_active_tasks(self, run_id: int) -> int:
        """Return number of tasks either pending or in progress."""

        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT COUNT(*)
                FROM crawl_tasks
                WHERE run_id = ?
                  AND status IN ('pending', 'in_progress')
                """,
                (run_id,),
            )
            value = cursor.fetchone()[0]
        return int(value)

    def count_tasks_by_status_for_source(self, source: str) -> Dict[str, int]:
        """Return aggregated task counts for an entire source across runs."""

        with self.connect() as connection:
            cursor = connection.execute(
                """
                SELECT ct.status, COUNT(*) AS count
                FROM crawl_tasks AS ct
                JOIN runs AS r ON r.id = ct.run_id
                WHERE r.source = ?
                GROUP BY ct.status
                """,
                (source,),
            )
            records = cursor.fetchall()

        return {row["status"]: int(row["count"]) for row in records}

    def get_task_status_counts(self, run_id: int) -> Dict[str, int]:
        """Return counts of tasks grouped by status."""

        with self.connect() as connection:
            cursor = connection.execute(
                "SELECT status, COUNT(*) as count FROM crawl_tasks WHERE run_id = ? GROUP BY status",
                (run_id,),
            )
            records = cursor.fetchall()
        return {row["status"]: int(row["count"]) for row in records}

    def count_open_exceptions(self, run_id: int) -> int:
        """Return number of unresolved exceptions for a run."""

        with self.connect() as connection:
            cursor = connection.execute(
                "SELECT COUNT(*) FROM exceptions WHERE run_id = ? AND status = 'open'",
                (run_id,),
            )
            value = cursor.fetchone()[0]
        return int(value)

    def acquire_tasks(
        self,
        run_id: int,
        limit: int,
        lease_owner: str,
        lease_seconds: float,
        max_retries: int,
        backoff_seconds: float,
    ) -> list["TaskRecord"]:
        """Claim pending tasks for processing."""

        now = datetime.now(timezone.utc)
        lease_expiry = now + timedelta(seconds=lease_seconds)
        now_str = now.strftime(ISO_FORMAT)
        lease_str = lease_expiry.strftime(ISO_FORMAT)
        next_run_str = (now + timedelta(seconds=backoff_seconds)).strftime(ISO_FORMAT)

        with self.connect() as connection:
            cursor = connection.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            if max_retries < 0:
                max_retries = 0
            cursor.execute(
                """
                UPDATE crawl_tasks
                SET status = 'error',
                    attempt_count = attempt_count + 1,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_run_at = ?,
                    last_error = 'lease expired',
                    updated_at = ?
                WHERE run_id = ?
                  AND status = 'in_progress'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                  AND attempt_count + 1 > ?
                """,
                (now_str, now_str, run_id, now_str, max_retries),
            )
            cursor.execute(
                """
                UPDATE crawl_tasks
                SET status = 'pending',
                    attempt_count = attempt_count + 1,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_run_at = ?,
                    last_error = 'lease expired',
                    updated_at = ?
                WHERE run_id = ?
                  AND status = 'in_progress'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                  AND attempt_count + 1 <= ?
                """,
                (next_run_str, now_str, run_id, now_str, max_retries),
            )
            rows = cursor.execute(
                """
                SELECT id, url, depth, status, attempt_count, lease_owner, lease_expires_at,
                       next_run_at, task_type
                FROM crawl_tasks
                WHERE run_id = ?
                  AND status = 'pending'
                  AND next_run_at <= ?
                ORDER BY priority DESC, id ASC
                LIMIT ?
                """,
                (run_id, now_str, limit),
            ).fetchall()

            task_ids = [row["id"] for row in rows]
            if task_ids:
                cursor.executemany(
                    """
                    UPDATE crawl_tasks
                    SET status = 'in_progress',
                        lease_owner = ?,
                        lease_expires_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    [(lease_owner, lease_str, now_str, task_id) for task_id in task_ids],
                )
            connection.commit()

        return [
            TaskRecord(
                id=row["id"],
                url=row["url"],
                depth=row["depth"],
                status="in_progress" if row["id"] in task_ids else row["status"],
                attempt_count=row["attempt_count"],
                lease_owner=lease_owner if row["id"] in task_ids else row["lease_owner"],
                lease_expires_at=lease_str if row["id"] in task_ids else row["lease_expires_at"],
                next_run_at=row["next_run_at"],
                task_type=row["task_type"],
            )
            for row in rows
        ]

    def complete_task(self, task_id: int) -> None:
        """Mark a task as finished."""

        now = _utcnow()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE crawl_tasks
                SET status = 'finished', lease_owner = NULL, lease_expires_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, task_id),
            )
            connection.commit()

    def record_asset(
        self,
        run_id: int,
        *,
        source_url: str,
        asset_key: str,
        asset_type: str,
        checksum: str,
        raw_path: Optional[str] = None,
        normalized_path: Optional[str] = None,
        metadata_json: Optional[str] = None,
    ) -> int:
        """Insert or update asset row and create a new version if needed."""

        now = _utcnow()
        with self.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO assets (run_id, source_url, asset_key, asset_type, status, checksum, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                ON CONFLICT(run_id, asset_key) DO UPDATE SET
                    checksum = excluded.checksum,
                    status = 'active',
                    updated_at = excluded.updated_at
                RETURNING id
                """,
                (run_id, source_url, asset_key, asset_type, checksum, now, now),
            )
            asset_id = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COALESCE(MAX(version), 0) FROM asset_versions WHERE asset_id = ?",
                (asset_id,),
            )
            next_version = cursor.fetchone()[0] + 1

            cursor.execute(
                """
                INSERT INTO asset_versions (
                    asset_id, version, checksum, created_at, raw_path, normalized_path, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    next_version,
                    checksum,
                    now,
                    raw_path,
                    normalized_path,
                    metadata_json,
                ),
            )

            connection.commit()

        return next_version

    def fail_task(
        self,
        task_id: int,
        *,
        error: str,
        backoff_seconds: float,
        max_retries: int = 0,
    ) -> None:
        """Return a task to the queue with backoff, or mark as error if retries exhausted."""

        now = datetime.now(timezone.utc)
        now_str = now.strftime(ISO_FORMAT)
        next_run = now + timedelta(seconds=backoff_seconds)
        with self.connect() as connection:
            if max_retries > 0:
                connection.execute(
                    """
                    UPDATE crawl_tasks
                    SET status = 'error',
                        attempt_count = attempt_count + 1,
                        last_error = ?,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        next_run_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND attempt_count + 1 >= ?
                    """,
                    (error, now_str, now_str, task_id, max_retries),
                )
            connection.execute(
                """
                UPDATE crawl_tasks
                SET status = 'pending',
                    attempt_count = attempt_count + 1,
                    last_error = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_run_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status != 'error'
                """,
                (
                    error,
                    next_run.strftime(ISO_FORMAT),
                    now_str,
                    task_id,
                ),
            )
            connection.commit()

    def mark_task_error(self, task_id: int, *, error: str) -> None:
        """Mark a task as permanently failed."""

        now = _utcnow()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE crawl_tasks
                SET status = 'error',
                    attempt_count = attempt_count + 1,
                    last_error = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_run_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error, now, now, task_id),
            )
            connection.commit()

    def release_task(self, task_id: int, *, reason: str = "interrupted") -> None:
        """Return an in-progress task to pending without counting as a retry."""

        now = _utcnow()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE crawl_tasks
                SET status = 'pending',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_run_at = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, reason, now, task_id),
            )
            connection.commit()

    def release_in_progress_tasks(self, run_id: int, *, reason: str = "interrupted") -> int:
        """Return all in-progress tasks for a run back to pending."""

        now = _utcnow()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE crawl_tasks
                SET status = 'pending',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_run_at = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE run_id = ?
                  AND status = 'in_progress'
                """,
                (now, reason, now, run_id),
            )
            connection.commit()
            return cursor.rowcount

    # --- Data Access Methods for CLI ---

    def get_latest_run(
        self, source: str, statuses: Optional[list[str]] = None
    ) -> Optional[RunRecord]:
        """Get most recent run for source, optionally filtered by status."""

        if statuses:
            placeholders = ",".join("?" * len(statuses))
            sql = f"""
                SELECT id, source, status, started_at, completed_at, label
                FROM runs
                WHERE source = ? AND status IN ({placeholders})
                ORDER BY started_at DESC
                LIMIT 1
            """
            params = [source] + statuses
        else:
            sql = """
                SELECT id, source, status, started_at, completed_at, label
                FROM runs
                WHERE source = ?
                ORDER BY started_at DESC
                LIMIT 1
            """
            params = [source]

        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()

        if row is None:
            return None

        return RunRecord(
            id=row["id"],
            source=row["source"],
            status=row["status"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            label=row["label"],
        )

    def list_assets(
        self,
        run_id: int,
        asset_type: Optional[str] = None,
        url_pattern: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list["AssetRecord"]:
        """List assets with filtering and pagination using optimized CTE."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                WITH latest_versions AS (
                    SELECT
                        asset_id,
                        MAX(version) as max_version,
                        COUNT(*) as version_count
                    FROM asset_versions
                    GROUP BY asset_id
                )
                SELECT
                    a.id,
                    a.run_id,
                    a.asset_key,
                    a.asset_type,
                    a.source_url,
                    a.checksum,
                    a.status,
                    a.created_at,
                    a.updated_at,
                    COALESCE(lv.version_count, 0) as version_count,
                    av.raw_path,
                    av.normalized_path,
                    av.metadata_json
                FROM assets a
                LEFT JOIN latest_versions lv ON lv.asset_id = a.id
                LEFT JOIN asset_versions av ON av.asset_id = a.id
                    AND av.version = lv.max_version
                WHERE a.run_id = ?
                    AND (? IS NULL OR a.asset_type = ?)
                    AND (? IS NULL OR a.asset_key GLOB ?)
                ORDER BY a.id DESC
                LIMIT ? OFFSET ?
                """,
                (run_id, asset_type, asset_type, url_pattern, url_pattern, limit, offset),
            ).fetchall()

        return [
            AssetRecord(
                id=row["id"],
                run_id=row["run_id"],
                asset_key=row["asset_key"],
                asset_type=row["asset_type"],
                source_url=row["source_url"],
                checksum=row["checksum"],
                status=row["status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                version_count=row["version_count"],
                latest_raw_path=row["raw_path"],
                latest_normalized_path=row["normalized_path"],
                latest_metadata=row["metadata_json"],
            )
            for row in rows
        ]

    def get_asset(self, asset_id: int) -> Optional["AssetRecord"]:
        """Get single asset by ID with latest version info."""

        with self.connect() as connection:
            row = connection.execute(
                """
                WITH latest_versions AS (
                    SELECT
                        asset_id,
                        MAX(version) as max_version,
                        COUNT(*) as version_count
                    FROM asset_versions
                    WHERE asset_id = ?
                    GROUP BY asset_id
                )
                SELECT
                    a.id,
                    a.run_id,
                    a.asset_key,
                    a.asset_type,
                    a.source_url,
                    a.checksum,
                    a.status,
                    a.created_at,
                    a.updated_at,
                    COALESCE(lv.version_count, 0) as version_count,
                    av.raw_path,
                    av.normalized_path,
                    av.metadata_json
                FROM assets a
                LEFT JOIN latest_versions lv ON lv.asset_id = a.id
                LEFT JOIN asset_versions av ON av.asset_id = a.id
                    AND av.version = lv.max_version
                WHERE a.id = ?
                """,
                (asset_id, asset_id),
            ).fetchone()

        if row is None:
            return None

        return AssetRecord(
            id=row["id"],
            run_id=row["run_id"],
            asset_key=row["asset_key"],
            asset_type=row["asset_type"],
            source_url=row["source_url"],
            checksum=row["checksum"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version_count=row["version_count"],
            latest_raw_path=row["raw_path"],
            latest_normalized_path=row["normalized_path"],
            latest_metadata=row["metadata_json"],
        )

    def get_asset_by_url(
        self, url: str, run_id: Optional[int] = None
    ) -> Optional["AssetRecord"]:
        """Get asset by URL (asset_key), optionally scoped to run."""

        if run_id is not None:
            where_clause = "WHERE a.asset_key = ? AND a.run_id = ?"
            params = (url, url, run_id)
        else:
            where_clause = "WHERE a.asset_key = ?"
            params = (url, url)

        with self.connect() as connection:
            row = connection.execute(
                f"""
                WITH latest_versions AS (
                    SELECT
                        asset_id,
                        MAX(version) as max_version,
                        COUNT(*) as version_count
                    FROM asset_versions
                    GROUP BY asset_id
                )
                SELECT
                    a.id,
                    a.run_id,
                    a.asset_key,
                    a.asset_type,
                    a.source_url,
                    a.checksum,
                    a.status,
                    a.created_at,
                    a.updated_at,
                    COALESCE(lv.version_count, 0) as version_count,
                    av.raw_path,
                    av.normalized_path,
                    av.metadata_json
                FROM assets a
                LEFT JOIN latest_versions lv ON lv.asset_id = a.id
                LEFT JOIN asset_versions av ON av.asset_id = a.id
                    AND av.version = lv.max_version
                {where_clause}
                ORDER BY a.updated_at DESC
                LIMIT 1
                """,
                params,
            ).fetchone()

        if row is None:
            return None

        return AssetRecord(
            id=row["id"],
            run_id=row["run_id"],
            asset_key=row["asset_key"],
            asset_type=row["asset_type"],
            source_url=row["source_url"],
            checksum=row["checksum"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version_count=row["version_count"],
            latest_raw_path=row["raw_path"],
            latest_normalized_path=row["normalized_path"],
            latest_metadata=row["metadata_json"],
        )

    def get_asset_version(
        self, asset_id: int, version: Optional[int] = None
    ) -> Optional["AssetVersionRecord"]:
        """Get specific version or latest if version is None."""

        if version is not None:
            sql = """
                SELECT id, asset_id, version, checksum, created_at,
                       raw_path, normalized_path, metadata_json
                FROM asset_versions
                WHERE asset_id = ? AND version = ?
            """
            params = (asset_id, version)
        else:
            sql = """
                SELECT id, asset_id, version, checksum, created_at,
                       raw_path, normalized_path, metadata_json
                FROM asset_versions
                WHERE asset_id = ?
                ORDER BY version DESC
                LIMIT 1
            """
            params = (asset_id,)

        with self.connect() as connection:
            row = connection.execute(sql, params).fetchone()

        if row is None:
            return None

        return AssetVersionRecord(
            id=row["id"],
            asset_id=row["asset_id"],
            version=row["version"],
            checksum=row["checksum"],
            created_at=row["created_at"],
            raw_path=row["raw_path"],
            normalized_path=row["normalized_path"],
            metadata_json=row["metadata_json"],
        )

    def list_tasks_for_run(
        self,
        run_id: int,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list["TaskRecord"]:
        """List crawl tasks with optional status filter."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, url, depth, status, attempt_count, lease_owner,
                       lease_expires_at, next_run_at, last_error, task_type
                FROM crawl_tasks
                WHERE run_id = ?
                    AND (? IS NULL OR status = ?)
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (run_id, status, status, limit, offset),
            ).fetchall()

        return [
            TaskRecord(
                id=row["id"],
                url=row["url"],
                depth=row["depth"],
                status=row["status"],
                attempt_count=row["attempt_count"],
                lease_owner=row["lease_owner"],
                lease_expires_at=row["lease_expires_at"],
                next_run_at=row["next_run_at"],
                last_error=row["last_error"],
                task_type=row["task_type"],
            )
            for row in rows
        ]

    # --- Source-level Methods for 0.4.0 ---

    def list_sources(self) -> list["SourceSummary"]:
        """List all sources with summary statistics."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    r.source as name,
                    COUNT(DISTINCT r.id) as run_count,
                    COUNT(a.id) as asset_count,
                    MAX(r.started_at) as last_run_at,
                    (SELECT status FROM runs r2
                     WHERE r2.source = r.source
                     ORDER BY started_at DESC LIMIT 1) as last_status
                FROM runs r
                LEFT JOIN assets a ON a.run_id = r.id
                GROUP BY r.source
                ORDER BY last_run_at DESC
                """
            ).fetchall()

        return [
            SourceSummary(
                name=row["name"],
                run_count=row["run_count"],
                asset_count=row["asset_count"],
                last_run_at=row["last_run_at"],
                last_status=row["last_status"],
            )
            for row in rows
        ]

    def get_source_summary(self, source: str) -> Optional["SourceSummary"]:
        """Get summary for a specific source."""

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    r.source as name,
                    COUNT(DISTINCT r.id) as run_count,
                    COUNT(a.id) as asset_count,
                    MAX(r.started_at) as last_run_at,
                    (SELECT status FROM runs r2
                     WHERE r2.source = r.source
                     ORDER BY started_at DESC LIMIT 1) as last_status
                FROM runs r
                LEFT JOIN assets a ON a.run_id = r.id
                WHERE r.source = ?
                GROUP BY r.source
                """,
                (source,),
            ).fetchone()

        if row is None:
            return None

        return SourceSummary(
            name=row["name"],
            run_count=row["run_count"],
            asset_count=row["asset_count"],
            last_run_at=row["last_run_at"],
            last_status=row["last_status"],
        )

    def get_source_stats(self, source: str) -> Optional["SourceStats"]:
        """Get detailed statistics for a source."""

        with self.connect() as connection:
            # Check source exists
            exists = connection.execute(
                "SELECT 1 FROM runs WHERE source = ? LIMIT 1", (source,)
            ).fetchone()
            if exists is None:
                return None

            # Run counts by status
            run_rows = connection.execute(
                """
                SELECT status, COUNT(*) as count
                FROM runs WHERE source = ?
                GROUP BY status
                """,
                (source,),
            ).fetchall()
            runs_by_status = {row["status"]: row["count"] for row in run_rows}

            # Asset counts by type
            asset_rows = connection.execute(
                """
                SELECT a.asset_type, COUNT(*) as count
                FROM assets a
                JOIN runs r ON a.run_id = r.id
                WHERE r.source = ?
                GROUP BY a.asset_type
                """,
                (source,),
            ).fetchall()
            assets_by_type = {row["asset_type"]: row["count"] for row in asset_rows}

            # Task counts by status
            task_rows = connection.execute(
                """
                SELECT t.status, COUNT(*) as count
                FROM crawl_tasks t
                JOIN runs r ON t.run_id = r.id
                WHERE r.source = ?
                GROUP BY t.status
                """,
                (source,),
            ).fetchall()
            tasks_by_status = {row["status"]: row["count"] for row in task_rows}

            # Timeline
            timeline = connection.execute(
                """
                SELECT
                    MIN(started_at) as first_run_at,
                    MAX(started_at) as last_run_at,
                    AVG(
                        CASE WHEN completed_at IS NOT NULL
                        THEN (julianday(completed_at) - julianday(started_at)) * 86400
                        ELSE NULL END
                    ) as avg_duration_seconds
                FROM runs
                WHERE source = ?
                """,
                (source,),
            ).fetchone()

            # File sizes - get paths and calculate
            path_rows = connection.execute(
                """
                SELECT av.raw_path, av.normalized_path
                FROM asset_versions av
                JOIN assets a ON av.asset_id = a.id
                JOIN runs r ON a.run_id = r.id
                WHERE r.source = ?
                """,
                (source,),
            ).fetchall()

        # Calculate file sizes (outside connection context)
        total_raw_bytes = 0
        total_normalized_bytes = 0
        for row in path_rows:
            if row["raw_path"]:
                p = Path(row["raw_path"])
                if p.exists():
                    total_raw_bytes += p.stat().st_size
            if row["normalized_path"]:
                p = Path(row["normalized_path"])
                if p.exists():
                    total_normalized_bytes += p.stat().st_size

        return SourceStats(
            name=source,
            runs_by_status=runs_by_status,
            assets_by_type=assets_by_type,
            tasks_by_status=tasks_by_status,
            total_raw_bytes=total_raw_bytes,
            total_normalized_bytes=total_normalized_bytes,
            first_run_at=timeline["first_run_at"] if timeline else None,
            last_run_at=timeline["last_run_at"] if timeline else None,
            avg_duration_seconds=timeline["avg_duration_seconds"] if timeline else None,
        )

    def get_asset_paths_for_source(
        self, source: str, raw: bool = False
    ) -> Iterator[tuple[int, str, Optional[str], Optional[str]]]:
        """Yield (asset_id, url, raw_path, normalized_path) for grep."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    a.id as asset_id,
                    a.asset_key as url,
                    av.raw_path,
                    av.normalized_path
                FROM assets a
                JOIN runs r ON a.run_id = r.id
                JOIN asset_versions av ON av.asset_id = a.id
                    AND av.version = (
                        SELECT MAX(version) FROM asset_versions WHERE asset_id = a.id
                    )
                WHERE r.source = ?
                ORDER BY a.id
                """,
                (source,),
            ).fetchall()

        for row in rows:
            yield (
                row["asset_id"],
                row["url"],
                row["raw_path"],
                row["normalized_path"],
            )

    def delete_source(self, source: str) -> "DeleteResult":
        """Delete all data for a source. Raises ValueError if runs in progress."""

        with self.connect() as connection:
            # Check for running status
            running = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE source = ? AND status = 'running'",
                (source,),
            ).fetchone()[0]
            if running > 0:
                raise ValueError(f"Cannot delete: {running} run(s) in progress")

            # Get file paths before deleting
            path_rows = connection.execute(
                """
                SELECT av.raw_path, av.normalized_path
                FROM asset_versions av
                JOIN assets a ON av.asset_id = a.id
                JOIN runs r ON a.run_id = r.id
                WHERE r.source = ?
                """,
                (source,),
            ).fetchall()

            # Count for result
            run_count = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE source = ?", (source,)
            ).fetchone()[0]
            asset_count = connection.execute(
                """
                SELECT COUNT(*) FROM assets a
                JOIN runs r ON a.run_id = r.id
                WHERE r.source = ?
                """,
                (source,),
            ).fetchone()[0]

            # Delete in FK order
            connection.execute(
                """
                DELETE FROM asset_versions
                WHERE asset_id IN (
                    SELECT a.id FROM assets a
                    JOIN runs r ON a.run_id = r.id
                    WHERE r.source = ?
                )
                """,
                (source,),
            )
            connection.execute(
                """
                DELETE FROM assets
                WHERE run_id IN (SELECT id FROM runs WHERE source = ?)
                """,
                (source,),
            )
            connection.execute(
                """
                DELETE FROM crawl_tasks
                WHERE run_id IN (SELECT id FROM runs WHERE source = ?)
                """,
                (source,),
            )
            connection.execute(
                """
                DELETE FROM exceptions
                WHERE run_id IN (SELECT id FROM runs WHERE source = ?)
                """,
                (source,),
            )
            connection.execute("DELETE FROM runs WHERE source = ?", (source,))
            connection.commit()

        # Delete files (best effort)
        files_deleted = 0
        bytes_freed = 0
        for row in path_rows:
            for path_str in [row["raw_path"], row["normalized_path"]]:
                if path_str:
                    p = Path(path_str)
                    if p.exists():
                        try:
                            bytes_freed += p.stat().st_size
                            p.unlink()
                            files_deleted += 1
                        except OSError:
                            pass  # Continue on error

        return DeleteResult(
            runs_deleted=run_count,
            assets_deleted=asset_count,
            files_deleted=files_deleted,
            bytes_freed=bytes_freed,
        )


@dataclass(slots=True)
class TaskRecord:
    """Snapshot of a crawl task."""

    id: int
    url: str
    depth: int
    status: str
    attempt_count: int
    lease_owner: Optional[str]
    lease_expires_at: Optional[str]
    next_run_at: str
    last_error: Optional[str] = None
    task_type: str = "page"


@dataclass(slots=True)
class AssetRecord:
    """Snapshot of an asset with latest version info."""

    id: int
    run_id: int
    asset_key: str
    asset_type: str
    source_url: str
    checksum: Optional[str]
    status: str
    created_at: str
    updated_at: str
    version_count: int = 0
    latest_raw_path: Optional[str] = None
    latest_normalized_path: Optional[str] = None
    latest_metadata: Optional[str] = None


@dataclass(slots=True)
class AssetVersionRecord:
    """Snapshot of a specific asset version."""

    id: int
    asset_id: int
    version: int
    checksum: str
    created_at: str
    raw_path: Optional[str] = None
    normalized_path: Optional[str] = None
    metadata_json: Optional[str] = None


@dataclass(slots=True)
class SourceSummary:
    """Summary statistics for a source."""

    name: str
    run_count: int
    asset_count: int
    last_run_at: Optional[str]
    last_status: Optional[str]


@dataclass(slots=True)
class SourceStats:
    """Detailed statistics for a source."""

    name: str
    runs_by_status: Dict[str, int]
    assets_by_type: Dict[str, int]
    tasks_by_status: Dict[str, int]
    total_raw_bytes: int
    total_normalized_bytes: int
    first_run_at: Optional[str]
    last_run_at: Optional[str]
    avg_duration_seconds: Optional[float]


@dataclass(slots=True)
class GrepMatch:
    """A single grep match result."""

    source: str
    asset_id: int
    url: str
    path: str
    line_no: int
    line: str
    context_before: list[str]
    context_after: list[str]


@dataclass(slots=True)
class DeleteResult:
    """Result of deleting a source."""

    runs_deleted: int
    assets_deleted: int
    files_deleted: int
    bytes_freed: int
