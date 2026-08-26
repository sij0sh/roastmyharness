"""Report collection selects one newest attempt per variant and task."""

from __future__ import annotations

import json
import os
from pathlib import Path

from roast_my_harness.report.collect import collect_rows


def _write_trial(root: Path, job: str, reward: float) -> Path:
    trial = root / "a" / job
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    result = trial / "result.json"
    result.write_text(
        json.dumps(
            {
                "task_name": "t1",
                "verifier_result": {"rewards": {"reward": reward}},
                "exception_info": {},
            }
        )
    )
    return result


def test_collect_rows_uses_newest_attempt(tmp_path: Path):
    older = _write_trial(tmp_path, "old", 0.0)
    newer = _write_trial(tmp_path, "new", 1.0)
    os.utime(older, (10, 10))
    os.utime(newer, (20, 20))
    rows = collect_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["reward"] == 1.0
