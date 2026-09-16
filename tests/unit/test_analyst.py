"""Analyst executive report: evidence wording, terminal sections, analysis key."""

import json

from roast_my_harness.report.analyst import analyze_run, render_markdown
from roast_my_harness.report.exports import write_summary_json
from roast_my_harness.report.metrics import analysis_series


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


ROWS = [
    _row("control", "t1", 1, partial=1.0),
    _row("control", "t2", 0, partial=0.2),
    _row("variant", "t1", 0, partial=0.9),
    _row("variant", "t2", 1, partial=1.0),
]

PROVENANCE = {"experiment_id": "exp-1", "spec": {"variants": []}}


def _write_summary(tmp_path):
    write_summary_json(tmp_path, ROWS, PROVENANCE)
    return tmp_path


def test_summary_json_uses_analysis_key(tmp_path):
    _write_summary(tmp_path)
    payload = json.loads((tmp_path / "summary.json").read_text())
    assert "analysis" in payload
    assert "charts" not in payload
    assert [r["variant"] for r in payload["analysis"]["resolve_rates"]] == [
        "control",
        "variant",
    ]


def test_analyze_marks_evidence_not_verdict(tmp_path):
    _write_summary(tmp_path)
    payload = analyze_run(tmp_path)
    assert payload["status"] == "ok"
    best = payload["best_arm"]
    assert best["variant"] == "variant"
    assert best["separated_from_control"] in (True, False, None)
    assert set(payload["rates"]) == {"control", "variant"}
    text = render_markdown(payload)
    assert "Highest observed resolve rate: variant" in text
    assert "Delta vs control:" in text
    assert "95% intervals:" in text
    assert "Best non-control arm" not in text


def test_render_is_terminal_report(tmp_path):
    _write_summary(tmp_path)
    text = render_markdown(analyze_run(tmp_path))
    for section in (
        "## Overall",
        "## Resolve rate",
        "## Paired outcomes",
        "## Token usage",
        "## Tool and read behavior",
        "## Runtime / cost",
        "## Interpretation",
        "## Artifacts",
    ):
        assert section in text
    assert ".png" not in text
    assert "charts/" not in text


def test_render_artifacts_first_with_file_links(tmp_path):
    _write_summary(tmp_path)
    payload = analyze_run(tmp_path)
    text = render_markdown(payload, tmp_path)
    first_section = text.index("## Artifacts")
    assert first_section < text.index("## Overall")
    for name in ("report.md", "summary.csv", "summary.json", "analysis.json"):
        assert f"[{name}](file://" in text


def test_analysis_payload_carries_tokens_and_tools(tmp_path):
    _write_summary(tmp_path)
    payload = analyze_run(tmp_path)
    assert payload["tokens"], "token series missing"
    assert payload["tools"], "tool series missing"
    by_variant = {entry["variant"]: entry for entry in payload["tokens"]}
    assert by_variant["variant"]["input_delta_pct"] is not None
    by_tool = {entry["variant"]: entry for entry in payload["tools"]}
    assert by_tool["variant"]["tool_calls_delta_vs_control"] is not None


def test_small_n_intervals_carry_uncertainty_caution(tmp_path):
    rows = [
        _row("control", "t1", 0, partial=0.0),
        _row("variant", "t1", 1, partial=1.0),
    ]
    write_summary_json(tmp_path, rows, PROVENANCE)
    text = render_markdown(analyze_run(tmp_path))
    assert "insufficient for a meaningful" in text
    assert "1 rescued and 0 broken" in text


def test_legacy_charts_payload_still_analyzes(tmp_path):
    series = analysis_series(ROWS, "exp-1")
    (tmp_path / "summary.json").write_text(
        json.dumps({"provenance": PROVENANCE, "trials": ROWS, "charts": series})
    )
    payload = analyze_run(tmp_path)
    assert payload["status"] == "ok"
    assert payload["best_arm"]["variant"] == "variant"
