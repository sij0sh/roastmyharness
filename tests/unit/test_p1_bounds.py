"""P1 hardening: declared bounds, enforced max_parallel, smoke probe,
consistent unsafe-source handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from roast_my_harness.errors import SpecError
from roast_my_harness.runner import probe as probe_mod
from roast_my_harness.spec.load import load_experiment
from roast_my_harness.spec.models import (
    ConcurrencySpec,
    ExperimentSpec,
    TaskSelection,
    VariantSpec,
)

BASE = """
schema_version = 1
name = "p1"
[tasks]
path = "{tasks}"
{extra}
[[variants]]
id = "a"
"""


def spec_text(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "exp.toml"
    path.write_text(BASE.format(tasks=tmp_path / "dataset", extra=extra))
    return path


# ------------------------------------------------------------- bounds ----

def test_per_variant_below_one_rejected(tmp_path: Path):
    path = spec_text(tmp_path, "[concurrency]\nper_variant = 0")
    with pytest.raises(SpecError, match="greater than or equal to 1"):
        load_experiment(path)


def test_http_egress_url_rejected(tmp_path: Path):
    path = spec_text(tmp_path, "")
    text = path.read_text().replace(
        "[[variants]]",
        "[[variants]]\negress_urls = [\"http://evil.example\"]",
        1,
    )
    path.write_text(text)
    with pytest.raises(SpecError, match="https://"):
        load_experiment(path)


def test_too_many_variants_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="at most 16 variants"):
        ExperimentSpec(
            name="t",
            tasks=TaskSelection(path=tmp_path),
            variants=[VariantSpec(id=f"v{i}") for i in range(17)],
        )


# -------------------------------------------------------- max_parallel ----

def test_max_parallel_divides_concurrency_across_arms():
    c = ConcurrencySpec(per_variant=4, max_parallel=6)
    assert c.effective_per_variant(3) == 2
    assert c.peak_parallel(3) == 6


def test_max_parallel_never_zero_for_tight_budget():
    c = ConcurrencySpec(per_variant=4, max_parallel=2)
    assert c.effective_per_variant(5) == 1
    assert c.peak_parallel(5) == 5  # 1 slot each, budget floor


def test_peak_concurrency_in_preview(tmp_path: Path):
    spec = ExperimentSpec(
        name="t",
        tasks=TaskSelection(path=tmp_path),
        control=None,
        concurrency=ConcurrencySpec(per_variant=4, max_parallel=6),
        variants=[VariantSpec(id="a"), VariantSpec(id="b")],
    )
    assert spec.peak_concurrency() == 6  # 2 arms x min(4, 6//2)


# -------------------------------------------------------- smoke probe ----

def test_should_probe_large_experiment(tmp_path):
    class Tasks:
        def __init__(self, n: int):
            for i in range(n):
                task = tmp_path / "dataset" / f"t{i}"
                task.mkdir(parents=True, exist_ok=True)
                (task / "task.toml").write_text('schema_version = "1.3"\n')

    class FakeSpec:
        def __init__(self, tasks_path, arms: int):
            self.tasks = type("T", (), {"path": tasks_path, "include": ["*"], "exclude": []})()
            self._arms = arms

        def arms(self):
            return list(range(self._arms))

    Tasks(10)
    assert probe_mod.should_probe(FakeSpec(tmp_path / "dataset", 2)) is True
    assert probe_mod.should_probe(FakeSpec(tmp_path / "dataset", 1)) is False


def test_probe_selects_extension_arm():
    class Ext:
        kind = "local"

    class Variant:
        id = "with-ext"
        extensions = [Ext()]

    class BareVariant:
        id = "control"
        extensions = []

    class Spec:
        variants = [BareVariant(), Variant()]

    jobs = {"control": object(), "with-ext": object()}
    assert probe_mod.select_variant(Spec(), jobs) == "with-ext"


def test_probe_result_ok():
    r = probe_mod.ProbeResult(
        state="passed", variant_id="a", task_id="t0",
        returncode=0, log_path=Path("/dev/null"),
    )
    assert r.ok
    r2 = probe_mod.ProbeResult(
        state="failed", variant_id="a", task_id="t0",
        returncode=2, log_path=Path("/dev/null"),
    )
    assert not r2.ok


# -------------------------------------------- unsafe-source consistency --

def test_no_allow_unsafe_source_flag_anywhere():
    """validate and run share one policy: world-writable sources always fail."""
    import inspect

    from roast_my_harness import cli
    from roast_my_harness.homes import builder

    validate_sig = inspect.signature(cli.validate)
    assert "allow_unsafe_source" not in validate_sig.parameters
    run_sig = inspect.signature(cli.run)
    assert "allow_unsafe_source" not in run_sig.parameters
    build_sig = inspect.signature(builder.build_home)
    assert "allow_unsafe_source" not in build_sig.parameters


# ------------------------------------------------- OAuth expiry preflight --

def test_preflight_fails_on_expired_oauth(monkeypatch, tmp_path):
    """Expired codex credentials must fail preflight with the login remedy."""
    from roast_my_harness.runner import preflight
    from roast_my_harness.spec.models import ModelSpec

    expired = {"access": "x", "type": "oauth", "expires": 1000}
    monkeypatch.setattr(
        "roast_my_harness.auth.service.codex_credential",
        lambda: expired,
    )

    class Spec:
        model = ModelSpec(provider="openai-codex", id="gpt-5")

        def arms(self):
            return []

    results = preflight._auth(Spec())
    assert len(results) == 1
    assert results[0].status == "fail"
    assert "pi /login codex" in results[0].detail
    assert preflight.has_failures(results)


def test_preflight_ok_on_valid_oauth(monkeypatch, tmp_path):
    import time

    from roast_my_harness.runner import preflight
    from roast_my_harness.spec.models import ModelSpec
    fresh = {"access": "x", "type": "oauth",
             "expires": (time.time() + 3600) * 1000}
    monkeypatch.setattr(
        "roast_my_harness.auth.service.codex_credential",
        lambda: fresh,
    )

    class Spec:
        model = ModelSpec(provider="openai-codex", id="gpt-5")

        def arms(self):
            return []

    results = preflight._auth(Spec())
    assert len(results) == 1
    assert results[0].status == "pass"
