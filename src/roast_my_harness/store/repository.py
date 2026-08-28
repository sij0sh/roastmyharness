"""Typed repository over the SQLite schema. Every write is transactional."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roast_my_harness import ADAPTER_PROTOCOL_VERSION, __version__
from roast_my_harness.store.database import connect
from roast_my_harness.store.migrations import apply_migrations


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Repository:
    def __init__(self, db_path: Path):
        self.conn: sqlite3.Connection = connect(db_path)
        apply_migrations(self.conn)

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------- experiments --

    def create_experiment(
        self, *, experiment_id: str, name: str, spec: dict, spec_hash: str,
        run_dir: str, status: str = "DRAFT",
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO experiments (id, name, spec_json, spec_hash, "
                "status, created_at, run_dir, tool_version, adapter_protocol) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO NOTHING",
                (
                    experiment_id, name, json.dumps(spec), spec_hash, status,
                    utc_now(), run_dir, __version__, ADAPTER_PROTOCOL_VERSION,
                ),
            )

    def set_status(
        self, experiment_id: str, status: str, *, started: bool = False,
        finished: bool = False,
    ) -> None:
        with self.conn:
            sets = ["status = ?"]
            args: list[Any] = [status]
            if started:
                sets.append("started_at = COALESCE(started_at, ?)")
                args.append(utc_now())
            if finished:
                sets.append("finished_at = ?")
                args.append(utc_now())
            args.append(experiment_id)
            self.conn.execute(
                f"UPDATE experiments SET {', '.join(sets)} WHERE id = ?", args
            )

    def get_experiment(self, experiment_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
        ).fetchone()

    def list_experiments(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM experiments ORDER BY created_at DESC"
        ).fetchall()

    # ---------------------------------------------------------- variants --

    def upsert_variant(
        self, experiment_id: str, variant_id: str, name: str | None,
        variant_hash: str, is_control: bool, manifest: dict | None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO variants (experiment_id, id, name, variant_hash, "
                "is_control, manifest_json) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(experiment_id, id) DO UPDATE SET "
                "variant_hash=excluded.variant_hash, "
                "manifest_json=excluded.manifest_json, name=excluded.name",
                (
                    experiment_id, variant_id, name, variant_hash,
                    int(is_control), json.dumps(manifest) if manifest else None,
                ),
            )

    # ------------------------------------------------------------ tasks --

    def upsert_tasks(
        self, experiment_id: str, tasks: list[tuple[str, str, str]]
    ) -> None:
        """tasks: (task_id, task_hash, source_path) tuples."""
        with self.conn:
            self.conn.executemany(
                "INSERT INTO tasks (experiment_id, task_id, task_hash, "
                "source_path) VALUES (?,?,?,?) "
                "ON CONFLICT(experiment_id, task_id) DO UPDATE SET "
                "task_hash=excluded.task_hash",
                [(experiment_id, *t) for t in tasks],
            )

    def get_tasks(self, experiment_id: str) -> list[sqlite3.Row]:
        """Stored task identity rows in insertion order."""
        return self.conn.execute(
            "SELECT task_id, task_hash FROM tasks WHERE experiment_id=? "
            "ORDER BY rowid",
            (experiment_id,),
        ).fetchall()

    # ---------------------------------------------------------- trials --

    def next_attempt(self, experiment_id: str, variant_id: str, task_id: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(attempt) FROM trials WHERE experiment_id=? "
            "AND variant_id=? AND task_id=?",
            (experiment_id, variant_id, task_id),
        ).fetchone()
        return (row[0] or 0) + 1

    def upsert_trial(
        self, *, experiment_id: str, variant_id: str, task_id: str, attempt: int,
        status: str, job_path: str | None, reward: float | None,
        resolved: bool | None, exception_type: str | None,
        metrics: dict | None, finished_at: str | None = None,
    ) -> str:
        existing = self.conn.execute(
            "SELECT id FROM trials WHERE experiment_id=? AND variant_id=? "
            "AND task_id=? AND attempt=?",
            (experiment_id, variant_id, task_id, attempt),
        ).fetchone()
        trial_id = str(existing["id"]) if existing else str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                "INSERT INTO trials (id, experiment_id, variant_id, task_id, "
                "attempt, status, job_path, reward, resolved, exception_type, "
                "metrics_json, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(experiment_id, variant_id, task_id, attempt) "
                "DO UPDATE SET status=excluded.status, "
                "job_path=excluded.job_path, reward=excluded.reward, "
                "resolved=excluded.resolved, exception_type=excluded.exception_type, "
                "metrics_json=excluded.metrics_json, "
                "finished_at=excluded.finished_at",
                (
                    trial_id, experiment_id, variant_id, task_id, attempt, status,
                    job_path, reward,
                    None if resolved is None else int(resolved),
                    exception_type,
                    json.dumps(metrics) if metrics else None,
                    finished_at,
                ),
            )
        return trial_id

    def upsert_reconciled_trial(
        self, *, experiment_id: str, variant_id: str, task_id: str,
        status: str, job_path: str | None, reward: float | None,
        resolved: bool | None, exception_type: str | None,
        metrics: dict | None, finished_at: str | None = None,
    ) -> str:
        """Persist one filesystem attempt without duplicating it on polling."""
        row = None
        if job_path is not None:
            row = self.conn.execute(
                "SELECT attempt FROM trials WHERE experiment_id=? "
                "AND variant_id=? AND task_id=? AND job_path=? "
                "ORDER BY attempt DESC LIMIT 1",
                (experiment_id, variant_id, task_id, job_path),
            ).fetchone()
        attempt = int(row["attempt"]) if row else self.next_attempt(
            experiment_id, variant_id, task_id
        )
        return self.upsert_trial(
            experiment_id=experiment_id,
            variant_id=variant_id,
            task_id=task_id,
            attempt=attempt,
            status=status,
            job_path=job_path,
            reward=reward,
            resolved=resolved,
            exception_type=exception_type,
            metrics=metrics,
            finished_at=finished_at,
        )


