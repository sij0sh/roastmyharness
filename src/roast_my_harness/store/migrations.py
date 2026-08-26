"""Explicit migrations. PRAGMA user_version is the source of truth."""

from __future__ import annotations

import sqlite3

MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE experiments (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        spec_json TEXT NOT NULL,
        spec_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        run_dir TEXT NOT NULL,
        tool_version TEXT NOT NULL,
        adapter_protocol INTEGER NOT NULL
    );
    CREATE TABLE variants (
        experiment_id TEXT REFERENCES experiments(id) ON DELETE CASCADE,
        id TEXT,
        name TEXT,
        variant_hash TEXT,
        is_control INTEGER NOT NULL DEFAULT 0,
        manifest_json TEXT,
        PRIMARY KEY (experiment_id, id)
    );
    CREATE TABLE tasks (
        experiment_id TEXT REFERENCES experiments(id) ON DELETE CASCADE,
        task_id TEXT,
        task_hash TEXT,
        source_path TEXT,
        PRIMARY KEY (experiment_id, task_id)
    );
    CREATE TABLE trials (
        id TEXT PRIMARY KEY,
        experiment_id TEXT REFERENCES experiments(id) ON DELETE CASCADE,
        variant_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL,
        job_path TEXT,
        started_at TEXT,
        finished_at TEXT,
        reward REAL,
        resolved INTEGER,
        exception_type TEXT,
        metrics_json TEXT,
        UNIQUE (experiment_id, variant_id, task_id, attempt)
    );
    CREATE TABLE control_observations (
        cohort_key TEXT,
        task_hash TEXT,
        trial_id TEXT,
        observed_at TEXT,
        resolved INTEGER,
        reward REAL,
        PRIMARY KEY (cohort_key, trial_id)
    );
    CREATE TABLE migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );
    """,
    2: """
    ALTER TABLE control_observations ADD COLUMN eligible INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE control_observations ADD COLUMN source TEXT NOT NULL DEFAULT 'run';
    CREATE INDEX idx_control_pool
        ON control_observations (cohort_key, task_hash, eligible, resolved);
    """,
}


def apply_migrations(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in sorted(MIGRATIONS):
        if version <= current:
            continue
        conn.execute("BEGIN")
        try:
            conn.executescript(MIGRATIONS[version])
            conn.execute(
                "INSERT INTO migrations (version, applied_at) "
                "VALUES (?, datetime('now'))",
                (version,),
            )
            conn.execute(f"PRAGMA user_version={version}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
