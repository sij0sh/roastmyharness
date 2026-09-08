"""Phase 9: explicit variant context files + frozen hypothesis + analyst."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roast_my_harness.adapter import command as cmd
from roast_my_harness.homes.builder import build_home, compute_source_hashes
from roast_my_harness.report import analyst
from roast_my_harness.report.statistics import variant_type
from roast_my_harness.spec.hashes import spec_hash
from roast_my_harness.spec.models import (
    ContextFileSpec,
    ExperimentSpec,
    TaskSelection,
    VariantSpec,
)
from roast_my_harness.spec.resolved import resolve_run_spec


def make_spec(tmp_path: Path, variants: list[VariantSpec], **over) -> ExperimentSpec:
    kw = dict(
        name="ctx",
        tasks=TaskSelection(path=tmp_path),
        control=None,
        variants=variants,
        pi_version="0.84.3",
    )
    kw.update(over)
    return ExperimentSpec(**kw)


def write_agents_md(root: Path, body: str = "Be brief.\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "AGENTS.md"
    path.write_text(body)
    return path


def test_context_file_spec_parses(tmp_path: Path):
    path = write_agents_md(tmp_path)
    spec = make_spec(
        tmp_path,
        [VariantSpec(id="a", context_files=[ContextFileSpec(path=path)])],
    )
    entry = spec.variants[0].context_files[0]
    assert entry.kind == "agents"
    assert entry.path == path


def test_context_file_rejects_bad_kind_and_name(tmp_path: Path):
    path = write_agents_md(tmp_path)
    with pytest.raises(Exception, match="kind"):
        ContextFileSpec(kind="prompt", path=path)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="context file name"):
        ContextFileSpec(path=path, name="../escape")


def test_context_files_rejected_for_incapable_agent(tmp_path: Path, monkeypatch):
    from roast_my_harness.adapter import registry

    monkeypatch.setitem(
        registry.AGENTS,
        "nocontext",
        registry.AgentDef(
            id="nocontext",
            family="other",
            import_path="somewhere:Agent",
            npm_package="some-package",
            binary="nocontext",
            home_env="NOCONTEXT_DIR",
            version_field="agent_version",
            fairness_flags="",
            default_version="1.0.0",
            supports_context_files=False,
        ),
    )
    path = write_agents_md(tmp_path)
    with pytest.raises(Exception, match="cannot deliver explicit context files"):
        make_spec(
            tmp_path,
            [
                VariantSpec(
                    id="a",
                    agent="nocontext",
                    context_files=[ContextFileSpec(path=path)],
                )
            ],
            agent="nocontext",
        )


def test_hypothesis_excluded_from_run_identity(tmp_path: Path):
    plain = make_spec(tmp_path, [VariantSpec(id="a")])
    noted = make_spec(tmp_path, [VariantSpec(id="a")], hypothesis="a beats bare")
    assert noted.hypothesis == "a beats bare"
    assert spec_hash(plain) == spec_hash(noted)
    assert resolve_run_spec(plain, []).run_id == resolve_run_spec(noted, []).run_id


def test_context_file_content_enters_variant_hash(tmp_path: Path):
    first = write_agents_md(tmp_path / "one", "Be brief.\n")
    second = write_agents_md(tmp_path / "two", "Be verbose.\n")
    from roast_my_harness.homes.builder import compute_variant_hash

    hash_a = compute_variant_hash(
        VariantSpec(id="a", context_files=[ContextFileSpec(path=first)]),
        "0.84.3",
    )
    hash_b = compute_variant_hash(
        VariantSpec(id="a", context_files=[ContextFileSpec(path=second)]),
        "0.84.3",
    )
    assert hash_a != hash_b
    hashes = compute_source_hashes(
        VariantSpec(id="a", context_files=[ContextFileSpec(path=first, name="guide")])
    )
    assert list(hashes) == ["ctx:guide"]


def test_context_file_staged_and_manifest_recorded(tmp_path: Path):
    path = write_agents_md(tmp_path / "repo")
    variant = VariantSpec(id="a", context_files=[ContextFileSpec(path=path)])
    home = build_home(variant, make_spec(tmp_path, [variant]), tmp_path / "homes")
    staged = home.path / "context-files" / "AGENTS.md"
    assert staged.read_text() == "Be brief.\n"
    assert home.manifest.context_files[0].path == "context-files/AGENTS.md"


def test_missing_context_file_fails_loud(tmp_path: Path):
    variant = VariantSpec(
        id="a", context_files=[ContextFileSpec(path=tmp_path / "gone.md")]
    )
    with pytest.raises(Exception, match="is not a file"):
        build_home(variant, make_spec(tmp_path, [variant]), tmp_path / "homes")


def test_context_prepend_format_and_empty_passthrough():
    assert cmd.with_context_files("Do it.", []) == "Do it."
    out = cmd.with_context_files(
        "Do it.", [("guide", "Be brief."), ("style", "No emojis.")]
    )
    assert out.startswith(
        '<roastmyharness-context-file name="guide">\nBe brief.\n</roastmyharness-context-file>'
    )
    assert "\n\nDo it." in out
    assert out.index("Be brief.") < out.index("Do it.")


def test_variant_type_names_context_files():
    variants = [
        {"id": "control"},
        {"id": "ctx", "context_files": [{"kind": "agents", "path": "x"}]},
        {
            "id": "mix",
            "extensions": [{"kind": "npm"}],
            "context_files": [{"kind": "agents", "path": "x"}],
        },
        {"id": "bare"},
    ]
    assert variant_type("control", variants) == "control"
    assert variant_type("ctx", variants) == "context_file"
    assert variant_type("mix", variants) == "extension+context_file"
    assert variant_type("bare", variants) == "bare"
    assert variant_type("stale", variants) == "unknown"


SUMMARY = {
    "provenance": {
        "experiment_id": "exp-1",
        "spec": {
            "hypothesis": "ctx beats bare",
            "variants": [
                {"id": "control"},
                {"id": "ctx", "context_files": [{"kind": "agents"}]},
            ],
        },
    },
    "trials": [
        {"variant": "control", "task": "t1", "replicate": 1, "resolved": 0},
        {"variant": "control", "task": "t2", "replicate": 1, "resolved": 1},
        {"variant": "ctx", "task": "t1", "replicate": 1, "resolved": 1},
        {"variant": "ctx", "task": "t2", "replicate": 1, "resolved": 1},
    ],
    "stratified": [
        {
            "stratum": "unlabeled",
            "variant": "control",
            "tasks": 2,
            "passed": 1,
            "total": 2,
            "rate": 0.5,
            "lo": 0.0,
            "hi": 1.0,
            "delta_pp_vs_control": None,
        },
        {
            "stratum": "unlabeled",
            "variant": "ctx",
            "tasks": 2,
            "passed": 2,
            "total": 2,
            "rate": 1.0,
            "lo": 1.0,
            "hi": 1.0,
            "delta_pp_vs_control": 50.0,
        },
    ],
}


def write_summary(root: Path) -> Path:
    (root / "summary.json").write_text(json.dumps(SUMMARY))
    return root


def test_analyst_reads_deterministic_evidence(tmp_path: Path):
    payload = analyst.analyze_run(write_summary(tmp_path))
    assert payload["status"] == "ok"
    assert payload["hypothesis"] == "ctx beats bare"
    assert payload["hypothesis_present"] is True
    assert payload["arms"]["ctx"] == {
        "type": "context_file",
        "resolved": 2,
        "total": 2,
        "score": 1.0,
    }
    assert payload["best_arm"]["variant"] == "ctx"
    assert payload["best_arm"]["delta_pp_vs_control"] == pytest.approx(50.0)
    unlabeled = payload["strata"][0]
    assert unlabeled["leader"] == "ctx"
    assert unlabeled["arms"]["ctx"]["separated_from_control"] is False


def test_analyst_fail_open_without_summary(tmp_path: Path):
    payload = analyst.analyze_run(tmp_path)
    assert payload["status"] == "unavailable"
    assert payload["reason"]
    out = analyst.write_analysis(tmp_path)
    assert out.is_file()
    assert (tmp_path / "analysis.md").is_file()
    assert "unavailable" in (tmp_path / "analysis.md").read_text()


def test_analysis_markdown_states_no_model_judgment(tmp_path: Path):
    analyst.write_analysis(write_summary(tmp_path))
    text = (tmp_path / "analysis.md").read_text()
    assert "no model judgment" in text
    assert "ctx beats bare" in text


SPEC_WITH_CTX = """
schema_version = 2
name = "ctxrun"
hypothesis = "ctx beats bare"
pi_version = "0.84.3"

[model]
id = "gpt-5.6-luna"
provider = "openai-codex"

[tasks]
path = "{tasks}"

[control]
enabled = true

[[variants]]
id = "guided"

[[variants.context_files]]
kind = "agents"
path = "{agents_md}"
"""


def _write_ctx_spec(tmp_path: Path, agents_md: Path) -> Path:
    dataset = tmp_path / "dataset" / "t1"
    dataset.mkdir(parents=True)
    (dataset / "task.toml").write_text('schema_version = "1.3"\n')
    path = tmp_path / "exp.toml"
    path.write_text(
        SPEC_WITH_CTX.format(tasks=tmp_path / "dataset", agents_md=agents_md)
    )
    return path


@pytest.fixture
def green_preflight(monkeypatch):
    import time

    from roast_my_harness.runner import preflight as pf

    def fake_run_checks(spec, *, skip_docker=False):
        return [pf._ok("python", "stubbed")]

    monkeypatch.setattr(pf, "run_checks", fake_run_checks)
    monkeypatch.setattr(
        "roast_my_harness.runner.pier.pier_version", lambda: "0.3.0"
    )
    fresh = {"access": "x", "type": "oauth", "expires": (time.time() + 3600) * 1000}
    monkeypatch.setattr(
        "roast_my_harness.auth.service.codex_credential", lambda: fresh
    )


def test_prepare_binds_context_file_and_hypothesis(tmp_path: Path, green_preflight):
    from roast_my_harness.agent import service as svc

    agents_md = write_agents_md(tmp_path / "repo")
    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    result = service.prepare(_write_ctx_spec(tmp_path, agents_md))
    assert result.ok is True
    assert result.experiment.hypothesis == "ctx beats bare"
    assert result.experiment.variant_sources == {"guided": []}
    plan = json.loads((tmp_path / "plans" / f"{result.plan_id}.json").read_text())
    assert "guided/ctx/AGENTS.md" in plan["bindings"]["source_hashes"]


def test_prepare_missing_context_file_is_needs_input(
    tmp_path: Path, green_preflight
):
    from roast_my_harness.agent import service as svc

    service = svc.AgentService(
        plans_dir=tmp_path / "plans", db_path=tmp_path / "db.sqlite"
    )
    result = service.prepare(_write_ctx_spec(tmp_path, tmp_path / "gone.md"))
    assert result.ok is False
    assert result.state == "needs_input"
    assert result.questions[0].field == "variants"
