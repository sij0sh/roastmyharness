"""Stratified reports: difficulty deltas, variant types, labeled coverage."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.report.exports import write_summary_json
from roast_my_harness.report.markdown import generate_report
from roast_my_harness.report.statistics import stratify, variant_type
from roast_my_harness.tasks.catalog import load_catalog

CATALOG = """
catalog_version = 1
benchmark = "mini"
benchmark_revision = "2026-09"

[tasks.t-easy]
difficulty = "easy"
basis_revision = "test"

[tasks.t-hard]
difficulty = "hard"
basis_revision = "test"
"""


def row(variant: str, task: str, resolved: int, replicate: int = 1) -> dict:
    return {
        "variant": variant,
        "task": task,
        "resolved": resolved,
        "replicate": replicate,
        "wall_time_sec": 60.0,
        "agent_time_sec": 50.0,
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "context_tokens": 0,
        "tool_calls": 3,
        "tool_results": 3,
        "tool_failures": 0,
        "tool_failure_rate": 0.0,
        "tool_missing_results": 0,
        "cost_usd": 0.01,
        "wall_sec": 60.0,
    }


def variants():
    return [
        {"id": "control"},
        {"id": "a", "extensions": [{"kind": "local"}]},
        {"id": "s", "skills": [{"kind": "local"}]},
        {"id": "b", "extensions": [{"kind": "local"}], "skills": [{"kind": "local"}]},
        {"id": "plain"},
    ]


def test_variant_type_classifies_arms():
    assert variant_type("control", variants()) == "control"
    assert variant_type("a", variants()) == "extension"
    assert variant_type("s", variants()) == "skill"
    assert variant_type("b", variants()) == "extension+skill"
    assert variant_type("plain", variants()) == "bare"
    assert variant_type("ghost", variants()) == "unknown"
    assert variant_type("c", [{"id": "c", "agents_md": "./AGENTS.md"}]) == "context_file"


def labels():
    return {
        "t-easy": {"difficulty": "easy", "duration": None},
        "t-hard": {"difficulty": "hard", "duration": None},
    }


def rows():
    return [
        row("control", "t-easy", 1),
        row("control", "t-hard", 0),
        row("control", "t-mystery", 1),
        row("a", "t-easy", 1),
        row("a", "t-hard", 1),
        row("a", "t-mystery", 0),
    ]


def test_stratify_groups_deltas_and_unlabeled():
    entries = stratify(rows(), labels(), seed=7)
    by_key = {(e["stratum"], e["variant"]): e for e in entries}
    assert set(by_key) == {
        ("easy", "control"), ("easy", "a"),
        ("hard", "control"), ("hard", "a"),
        ("unlabeled", "control"), ("unlabeled", "a"),
    }
    assert by_key[("easy", "a")]["delta_pp_vs_control"] == 0.0
    assert by_key[("hard", "a")]["delta_pp_vs_control"] == 100.0
    assert by_key[("easy", "control")]["delta_pp_vs_control"] is None
    assert by_key[("hard", "control")]["tasks"] == 1
    assert by_key[("hard", "control")]["passed"] == 0
    # Deterministic for the same seed.
    assert stratify(rows(), labels(), seed=7) == entries


def test_stratified_section_renders(tmp_path: Path):
    bench = tmp_path / "bench"
    bench.mkdir()
    (bench / "catalog.toml").write_text(CATALOG)
    provenance = {
        "experiment_id": "e",
        "tasks_path": str(bench),
        "spec": {"variants": variants()},
    }
    report = generate_report(
        tmp_path, experiment_id="e", rows=rows(), provenance=provenance
    )
    text = report.read_text()
    assert "## Results by task difficulty" in text
    assert "| easy | a | 1 | 1/1 | 100.0%" in text
    assert "| hard | a | 1 | 1/1 | 100.0% |" in text
    assert "+100.0pp" in text
    assert "| unlabeled | control | 1 | 1/1 |" in text
    assert "Duration axis omitted" in text
    assert "| a | extension |" in text
    assert "| control | control |" in text


def test_summary_json_carries_types_and_strata(tmp_path: Path):
    bench = tmp_path / "bench"
    bench.mkdir()
    (bench / "catalog.toml").write_text(CATALOG)
    provenance = {
        "experiment_id": "e",
        "tasks_path": str(bench),
        "spec": {"variants": variants()},
    }
    path = write_summary_json(tmp_path, rows(), provenance)
    payload = json.loads(path.read_text())
    assert payload["variant_types"] == {"a": "extension", "control": "control"}
    strata = {(e["stratum"], e["variant"]): e for e in payload["stratified"]}
    assert strata[("easy", "a")]["rate"] == 1.0
    assert strata[("hard", "control")]["rate"] == 0.0


def test_shipped_catalog_labels_are_attributable():
    root = Path(__file__).resolve().parents[2] / "tasks" / "deepswe" / "tasks"
    catalog = load_catalog(root)
    assert catalog is not None
    assert len(catalog.tasks) == 12
    from roast_my_harness.tasks.discover import is_task_dir

    for task_id, meta in catalog.tasks.items():
        assert meta.difficulty in ("easy", "medium", "hard")
        assert meta.basis_revision == "deepswe-published-mini-swe-agent"
        assert (meta.basis_samples or 0) >= 3
        assert "Pi-agent rate unmeasured" in meta.basis
        assert is_task_dir(root / task_id), task_id
