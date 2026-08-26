"""DSE importer: layout, eligibility, terminal-outcome extraction."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.report.importer import import_dse_results
from roast_my_harness.store.repository import Repository


def make_trial(
    root: Path, variant: str, job: str, task: str, *,
    reward: float | None = 1.0, exception: dict | None = None,
    checksum: str = "c0ffee",
) -> Path:
    trial = root / variant / job / f"{task}__abc123"
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir(parents=True)
    result = {
        "task_name": f"datacurve/{task}",
        "task_checksum": checksum,
        "trial_uri": f"file://{trial}",
        "finished_at": "2026-08-18T14:13:46.637473Z",
        "exception_info": exception or {},
        "verifier_result": (
            {"rewards": {"reward": reward}} if reward is not None else None
        ),
    }
    (trial / "result.json").write_text(json.dumps(result))
    return trial


def test_import_records_ineligible_observations(tmp_path: Path):
    root = tmp_path / "results"
    make_trial(root, "bare", "2026-08-18__05-57-51", "task-a", reward=1.0)
    make_trial(root, "bare", "2026-08-18__05-57-51", "task-b", reward=0.2)
    make_trial(
        root, "cm", "2026-08-18__05-57-51", "task-a", reward=1.0
    )  

    repo = Repository(tmp_path / "db.sqlite")
    report = import_dse_results(repo, root)
    assert report.scanned == 2  
    assert report.imported == 2
    assert report.skipped_variant == 0

    pool_all = repo.conn.execute(
        "SELECT * FROM control_observations WHERE task_hash LIKE 'legacy-dse:%'"
    ).fetchall()
    assert len(pool_all) == 2

    eligible = repo.control_pool(
        "legacy-dse:bare:c0ffee", "legacy-dse:c0ffee"
    )
    assert eligible == []  
    ineligible = repo.control_pool(
        "legacy-dse:bare:c0ffee", "legacy-dse:c0ffee", eligible_only=False
    )
    assert len(ineligible) == 2
    resolved = sorted(r["resolved"] for r in ineligible)
    assert resolved == [0, 1]  
    repo.close()


def test_import_skips_errors_and_missing_rewards(tmp_path: Path):
    root = tmp_path / "results"
    make_trial(
        root, "bare", "j1", "task-e", reward=1.0,
        exception={"type": "AgentTimeoutError"},
    )
    make_trial(root, "bare", "j1", "task-n", reward=None)
    make_trial(root, "bare", "j1", "task-ok", reward=1.0)

    repo = Repository(tmp_path / "db.sqlite")
    report = import_dse_results(repo, root)
    assert report.imported == 1
    assert report.skipped_error == 2
    repo.close()


def test_import_respects_variant_filter(tmp_path: Path):
    root = tmp_path / "results"
    make_trial(root, "bare", "j1", "task-a")
    make_trial(root, "cm", "j1", "task-a")

    repo = Repository(tmp_path / "db.sqlite")
    report = import_dse_results(repo, root, variants=["cm"])
    assert report.scanned == 1
    rows = repo.conn.execute(
        "SELECT cohort_key FROM control_observations"
    ).fetchall()
    assert len(rows) == 1
    assert "legacy-dse:cm:" in rows[0]["cohort_key"]
    repo.close()
