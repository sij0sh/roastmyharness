from roast_my_harness.report.metrics import (
    analysis_series,
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


def test_analysis_series_stable_and_degrading():
    rows = [
        _row("control", "t1", 1),
        _row("variant", "t1", 0),
    ]
    first = analysis_series(rows, "exp-1")
    second = analysis_series(rows, "exp-1")
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


def test_token_series_totals_and_deltas():
    from roast_my_harness.report.metrics import token_series

    rows = [
        _row("control", "t1", 1, input_tokens=100, output_tokens=200,
             cache_tokens=50, cache_write_tokens=10, reasoning_tokens=5,
             llm_calls=4),
        _row("control", "t2", 0, input_tokens=300, output_tokens=400,
             cache_tokens=150, cache_write_tokens=30, reasoning_tokens=15,
             llm_calls=6),
        _row("variant", "t1", 1, input_tokens=200, output_tokens=200,
             cache_tokens=100, cache_write_tokens=20, reasoning_tokens=10,
             llm_calls=5),
        _row("variant", "t2", 0, input_tokens=200, output_tokens=200,
             cache_tokens=100, cache_write_tokens=20, reasoning_tokens=10,
             llm_calls=5),
    ]
    by_variant = {entry["variant"]: entry for entry in token_series(rows)}
    assert by_variant["control"]["input_total"] == 400
    assert by_variant["control"]["input_mean"] == 200
    assert by_variant["variant"]["input_mean"] == 200
    assert by_variant["variant"]["input_delta_pct"] == 0.0
    assert by_variant["control"]["input_delta_pct"] is None
    # Legacy keys survive.
    assert by_variant["control"]["mean_input"] == 200
    assert by_variant["control"]["mean_cache_read"] == 100


def test_tool_series_absolute_deltas():
    from roast_my_harness.report.metrics import tool_series

    rows = [
        _row("control", "t1", 1, tool_calls=10, read_calls=6, read_rereads=2,
             read_overlap_rereads=1, distinct_read_files=3, tool_failures=1),
        _row("variant", "t1", 1, tool_calls=6, read_calls=4, read_rereads=1,
             read_overlap_rereads=0, distinct_read_files=2, tool_failures=0),
    ]
    by_variant = {entry["variant"]: entry for entry in tool_series(rows)}
    assert by_variant["variant"]["tool_calls_delta_vs_control"] == -4.0
    assert by_variant["variant"]["read_calls_delta_vs_control"] == -2.0
    assert by_variant["control"]["tool_calls_delta_vs_control"] is None
    assert by_variant["variant"]["mean_reads_per_file"] == 2.0
