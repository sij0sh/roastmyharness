from roast_my_harness.report.charts import (
    chart_series,
    near_miss_series,
    outcome_label,
    partial_delta_series,
)
from roast_my_harness.report.norms import build_norms, flag_outliers
from roast_my_harness.telemetry.result import COLUMNS, split_tests


def _row(variant, task, resolved, **extra):
    row = {
        "variant": variant,
        "task": task,
        "resolved": resolved,
        "reward": float(resolved),
        "exception_type": "",
        "replicate": 1,
        "input_tokens": 100,
        "output_tokens": 200,
        "wall_sec": 60.0,
        "cost_usd": 0.01,
    }
    row.update(extra)
    return row


def test_split_tests_promotes_counts():
    rewards = {
        "reward": 0,
        "f2p_total": 24,
        "f2p_passed": 22,
        "p2p_total": 162,
        "p2p_passed": 162,
        "partial": 0.989,
    }
    out = split_tests(rewards)
    assert out["f2p_total"] == 24
    assert out["f2p_passed"] == 22
    assert out["tests_total"] == 186
    assert out["tests_passed"] == 184
    assert out["partial"] == 0.989


def test_split_tests_absent_breakdown():
    out = split_tests({"reward": 1})
    assert all(value == "" for value in out.values())
    assert set(out) <= set(COLUMNS)


def test_outcome_labels():
    assert outcome_label(_row("c", "t", 1)) == "pass"
    assert outcome_label(_row("c", "t", 0, partial=0.95)) == "near-miss"
    assert outcome_label(_row("c", "t", 0, partial=0.5)) == "fail"
    assert outcome_label(_row("c", "t", 0)) == "fail"
    assert outcome_label(_row("c", "t", 0, exception_type="Timeout")) == "error"


def test_near_miss_series_buckets():
    rows = [
        _row("control", "t1", 0, partial=0.2),
        _row("control", "t2", 0, partial=0.6),
        _row("control", "t3", 0, partial=0.95),
        _row("control", "t4", 1, partial=1.0),
    ]
    arms = near_miss_series(rows)["arms"]
    assert arms["control"] == {"<0.5": 1, "0.5-0.8": 1, "0.8-1.0": 1}


def test_partial_delta_surfaces_quiet_moves():
    rows = [
        _row("control", "t1", 0, partial=0.5),
        _row("control", "t2", 1, partial=1.0),
        _row("variant", "t1", 0, partial=0.9),
        _row("variant", "t2", 1, partial=1.0),
    ]
    deltas = partial_delta_series(rows)
    assert len(deltas) == 1
    assert deltas[0]["task"] == "t1"
    assert abs(deltas[0]["delta"] - 0.4) < 1e-9


def test_chart_series_stable_and_degrading():
    rows = [
        _row("control", "t1", 1),
        _row("variant", "t1", 0),
    ]
    first = chart_series(rows, "exp-1")
    second = chart_series(rows, "exp-1")
    assert first == second
    assert first["arms"]["control"]["near_miss"] == 0
    assert first["arms"]["control"]["mean_partial"] is None
    assert first["partial_deltas"] == []


def test_norms_flag_runaway_output():
    base = {
        "variant": "control",
        "model": "m",
        "thinking": "high",
        "task": "t",
        "resolved": 0,
        "exception_type": "",
    }
    trials = [
        dict(base, partial=0.5, output_tokens=10000 + 100 * i, wall_sec=300.0)
        for i in range(4)
    ]
    norms = build_norms(trials)
    key = "m\0high\0t"
    assert norms["tasks"][key]["output_tokens"]["n"] == 4
    flagged = flag_outliers(
        [dict(base, variant="variant", partial=0.5, output_tokens=90000, wall_sec=300.0)],
        norms,
    )
    assert [(f["metric"], f["variant"]) for f in flagged] == [("output_tokens", "variant")]
    quiet = flag_outliers(
        [dict(base, variant="variant", partial=0.5, output_tokens=10100, wall_sec=300.0)],
        norms,
    )
    assert quiet == []
