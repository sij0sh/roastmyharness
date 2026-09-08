"""`tool catalog` and `tool history-availability`: wizard machine contract."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from roast_my_harness.cli import tool_app

CATALOG = """
catalog_version = 1
benchmark = "mini"
benchmark_revision = "2026-09"
source = "test"

[presets.quick]
label = "Quick"
tasks = ["t1", "t2"]

[tasks.t1]
duration = "fast"
difficulty = "easy"
smoke = true
estimated_minutes = 8
basis_revision = "mini-2026-09"
basis_samples = 10
basis_date = "2026-09-01"
"""

PROFILES = """
profiles_version = 1
benchmark = "mini"
benchmark_revision = "2026-09"

[[profiles]]
id = "mid"
label = "Mid"
provider = "p"
model = "m"
thinking = "high"
benchmark_rate = 0.5
benchmark_samples = 100
benchmark_revision = "pub"
basis = "test"
"""


def make_benchmark(root: Path) -> Path:
    for task_id in ("t1", "t2"):
        task = root / task_id
        task.mkdir(parents=True, exist_ok=True)
        (task / "task.toml").write_text('schema_version = "1.3"\n')
        (task / "instruction.md").write_text(f"{task_id}\n")
    (root / "catalog.toml").write_text(CATALOG)
    (root / "profiles.toml").write_text(PROFILES)
    return root


def test_tool_catalog_reports_presets_profiles_labels(tmp_path: Path):
    root = make_benchmark(tmp_path / "bench")
    result = CliRunner().invoke(tool_app, ["catalog", "--tasks", str(root)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["benchmark"] == "mini"
    assert payload["revision"] == "2026-09"
    assert payload["task_count"] == 2
    assert payload["presets"] == [
        {"id": "quick", "label": "Quick", "count": 2, "tasks": ["t1", "t2"]}
    ]
    assert [row["id"] for row in payload["profiles"]] == ["mid"]
    assert payload["profiles"][0]["expected_rate"] == 0.5
    assert payload["labels"]["t1"]["estimated_minutes"] == 8
    assert "t2" not in payload["labels"]


def test_tool_catalog_without_files_exits_nonzero(tmp_path: Path):
    result = CliRunner().invoke(tool_app, ["catalog", "--tasks", str(tmp_path)])
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "no_catalog"
    assert result.exit_code == 1


def test_tool_history_availability_bad_spec_reports_unavailable(tmp_path: Path):
    bad = tmp_path / "bad.toml"
    bad.write_text("bogus = true\n")
    result = CliRunner().invoke(tool_app, ["history-availability", str(bad)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["available"] is False
    assert payload["reason"]
