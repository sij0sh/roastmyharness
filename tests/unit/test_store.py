"""SQLite migrations and repository writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from roast_my_harness.errors import RunBusyError
from roast_my_harness.store.database import connect
from roast_my_harness.store.locking import ExperimentLock
from roast_my_harness.store.migrations import MIGRATIONS, apply_migrations
from roast_my_harness.store.repository import Repository


def test_migrations_idempotent(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    apply_migrations(conn)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == max(MIGRATIONS)
    apply_migrations(conn)  # no-op, no error
    assert conn.execute("PRAGMA user_version").fetchone()[0] == version


def test_experiment_lifecycle(tmp_path: Path):
    repo = Repository(tmp_path / "t.db")
    repo.create_experiment(
        experiment_id="e1", name="demo", spec={"name": "demo"},
        spec_hash="h" * 64, run_dir=str(tmp_path), status="DRAFT",
    )
    repo.upsert_variant("e1", "control", None, "v" * 64, True, {"x": 1})
    repo.upsert_tasks("e1", [("t1", "a" * 64, "/tmp/t1")])
    repo.upsert_trial(
        experiment_id="e1", variant_id="control", task_id="t1", attempt=1,
        status="pass", job_path="/x", reward=1.0, resolved=True,
        exception_type=None, metrics=None,
    )
    # upsert same attempt updates rather than duplicates
    repo.upsert_trial(
        experiment_id="e1", variant_id="control", task_id="t1", attempt=1,
        status="fail", job_path="/x", reward=0.0, resolved=False,
        exception_type=None, metrics=None,
    )
    rows = repo.conn.execute(
        "SELECT * FROM trials WHERE experiment_id=?", ("e1",)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "fail"
    assert repo.next_attempt("e1", "control", "t1") == 2
    row = repo.get_experiment("e1")
    assert row["status"] == "DRAFT"
    repo.close()


def test_reconciled_trial_is_idempotent(tmp_path: Path):
    repo = Repository(tmp_path / "t.db")
    repo.create_experiment(
        experiment_id="e", name="n", spec={}, spec_hash="h", run_dir="/tmp",
    )
    first = repo.upsert_reconciled_trial(
        experiment_id="e",
        variant_id="a",
        task_id="t1",
        status="pass",
        job_path="/run/jobs/a/t1__1",
        reward=1.0,
        resolved=True,
        exception_type=None,
        metrics=None,
    )
    second = repo.upsert_reconciled_trial(
        experiment_id="e",
        variant_id="a",
        task_id="t1",
        status="fail",
        job_path="/run/jobs/a/t1__1",
        reward=0.5,
        resolved=False,
        exception_type=None,
        metrics=None,
    )
    rows = repo.conn.execute("SELECT * FROM trials").fetchall()
    assert second == first
    assert len(rows) == 1
    assert rows[0]["status"] == "fail"
    assert rows[0]["attempt"] == 1
    repo.close()


def test_experiment_lock_is_exclusive(tmp_path: Path):
    run_dir = tmp_path / "run"
    with ExperimentLock(run_dir):
        with pytest.raises(RunBusyError, match="experiment is busy"):
            with ExperimentLock(run_dir):
                pass


def test_control_pool_roundtrip(tmp_path: Path):
    repo = Repository(tmp_path / "t.db")
    repo.create_experiment(
        experiment_id="e", name="n", spec={}, spec_hash="h", run_dir="/tmp",
    )
    repo.record_control_observation("k", "t", "trial-1", True, 1.0, "2026-01-01")
    repo.record_control_observation("k", "t", "trial-1", True, 1.0, "2026-01-01")
    pool = repo.control_pool("k", "t")
    assert len(pool) == 1
    repo.close()
