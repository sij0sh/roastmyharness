"""Named scoring dimensions: extraction, aggregation, reporting."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.report.dimensions import (
    dimension_summary,
    has_dimensions,
)
from roast_my_harness.report.exports import write_summary_csv, write_summary_json
from roast_my_harness.report.markdown import generate_report
from roast_my_harness.telemetry.result import split_dimensions, trial_row


def _write_trial(
    root: Path,
    variant: str,
    job: str,
    rewards: dict,
    *,
    task: str = "t1",
    exception: str = "",
) -> Path:
    trial = root / variant / job
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    result = trial / "result.json"
    result.write_text(
        json.dumps(
            {
                "task_name": task,
                "verifier_result": {"rewards": rewards},
                "exception_info": {"exception_type": exception} if exception else {},
            }
        )
    )
    return result


def test_split_dimensions_parsing():
    assert split_dimensions({}) == {
        "reward_deterministic": "",
        "reward_judge": "",
        "judge_model": "",
    }
    parsed = split_dimensions(
        {
            "reward": 1.0,
            "reward_deterministic": "0.5",
            "reward_judge": 0.0,
            "judge_model": "m1",
            "other": "kept-out",
        }
    )
    assert parsed["reward_deterministic"] == 0.5
    # Zero is a reported score, not absence.
    assert parsed["reward_judge"] == 0.0
    assert parsed["judge_model"] == "m1"
    assert "other" not in parsed
    assert split_dimensions({"reward_deterministic": "nan-x"})[
        "reward_deterministic"
    ] == ""


def test_trial_row_carries_dimensions(tmp_path: Path):
    result = _write_trial(
        tmp_path,
        "a",
        "job",
        {"reward": 1.0, "reward_deterministic": 0.8, "reward_judge": 0.6},
    )
    row = trial_row(result, "a")
    assert row is not None
    assert row["reward_deterministic"] == 0.8
    assert row["reward_judge"] == 0.6
    assert row["judge_model"] == ""


def test_trial_row_without_dimensions_reads_empty(tmp_path: Path):
    result = _write_trial(tmp_path, "a", "job", {"reward": 1.0})
    row = trial_row(result, "a")
    assert row is not None
    assert row["reward_deterministic"] == ""
    assert not has_dimensions([row])


def _dim_rows() -> list[dict]:
    base = {"input_tokens": "", "output_tokens": "", "cost_usd": "", "wall_sec": ""}
    return [
        {"variant": "a", "task": "t1", "replicate": 1, "resolved": 1,
         "reward_deterministic": 1.0, "reward_judge": 0.5, "judge_model": "m1",
         **base},
        {"variant": "a", "task": "t1", "replicate": 2, "resolved": 0,
         "reward_deterministic": 0.0, "reward_judge": 0.5, "judge_model": "m1",
         **base},
        {"variant": "a", "task": "t2", "replicate": 1, "resolved": 1,
         "reward_deterministic": 0.5, "reward_judge": "", "judge_model": "",
         **base},
        # Infra-error trials never enter dimension aggregates.
        {"variant": "a", "task": "t3", "replicate": 1, "resolved": 0,
         "reward_deterministic": 1.0, "reward_judge": 1.0, "judge_model": "m1",
         "exception_type": "INFRA_X", **base},
        {"variant": "b", "task": "t1", "replicate": 1, "resolved": 1,
         "reward_deterministic": "", "reward_judge": "", "judge_model": "",
         **base},
    ]


def test_has_dimensions():
    assert has_dimensions(_dim_rows())
    assert not has_dimensions(
        [r for r in _dim_rows() if r["variant"] == "b"]
    )


def test_dimension_summary_math():
    summary = dimension_summary(_dim_rows(), seed=7)
    det = summary["a"]["reward_deterministic"]
    # Per-task means: t1 = 0.5, t2 = 0.5 (t3 excluded as infra error).
    assert det["mean"] == 0.5
    assert det["tasks"] == 2
    assert det["lo"] <= det["mean"] <= det["hi"]
    judge = summary["a"]["reward_judge"]
    assert judge["mean"] == 0.5
    assert judge["tasks"] == 1
    assert summary["a"]["judge_models"] == ["m1"]
    assert "b" not in summary


def test_summary_json_dimensions_only_when_present(tmp_path: Path):
    provenance: dict = {"experiment_id": "exp", "spec": {"variants": []}}
    out = write_summary_json(tmp_path, _dim_rows(), provenance)
    payload = json.loads(out.read_text())
    assert set(payload["dimensions"]["a"]) >= {
        "reward_deterministic",
        "reward_judge",
        "judge_models",
    }
    plain = [dict(r, reward_deterministic="", reward_judge="") for r in _dim_rows()]
    out = write_summary_json(tmp_path, plain, provenance)
    assert "dimensions" not in json.loads(out.read_text())


def test_report_section_only_when_present(tmp_path: Path):
    provenance: dict = {"experiment_id": "exp", "spec": {"variants": []}}
    out = generate_report(
        tmp_path, experiment_id="exp", provenance=provenance, rows=_dim_rows()
    )
    text = out.read_text()
    assert "## Scores by dimension" in text
    assert "m1" in text
    plain = [dict(r, reward_deterministic="", reward_judge="") for r in _dim_rows()]
    out = generate_report(
        tmp_path, experiment_id="exp", provenance=provenance, rows=plain
    )
    assert "## Scores by dimension" not in out.read_text()


def test_csv_appends_dimension_columns(tmp_path: Path):
    result = _write_trial(
        tmp_path,
        "a",
        "job",
        {"reward": 1.0, "reward_deterministic": 0.8, "judge_model": "m1"},
    )
    from roast_my_harness.report.collect import collect_rows

    rows = collect_rows(tmp_path)
    assert rows and rows[0]["reward_deterministic"] == 0.8
    out = write_summary_csv(tmp_path, rows)
    header = out.read_text().splitlines()[0]
    assert "reward_deterministic" in header
    assert "reward_judge" in header
    assert "judge_model" in header
    assert result.exists()
