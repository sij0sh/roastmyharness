"""Eval registry: bundled DeepSWE plus external roots."""

from __future__ import annotations

from pathlib import Path

import pytest

from roast_my_harness.evals.registry import eval_label, resolve_eval
from roast_my_harness.spec.models import ExperimentSpec, TaskSelection, VariantSpec


def make_spec(tasks: Path, **kw) -> ExperimentSpec:
    base = dict(name="e", tasks=TaskSelection(path=tasks), variants=[VariantSpec(id="a")])
    base.update(kw)
    return ExperimentSpec(**base)


def test_default_eval_resolves_none(tmp_path: Path):
    assert resolve_eval(make_spec(tmp_path), tmp_path) is None
    assert eval_label(make_spec(tmp_path)) == "bundled/deepswe"


def test_unknown_bundled_rejected(tmp_path: Path):
    from pydantic import ValidationError

    from roast_my_harness.spec.models import EvalSpec
    with pytest.raises(ValidationError):
        EvalSpec(type="bundled", id="nope")


def test_external_without_descriptor(tmp_path: Path):
    from roast_my_harness.spec.models import EvalSpec
    spec = make_spec(tmp_path, evaluation=EvalSpec(type="external", id="mine"))
    frozen = resolve_eval(spec, tmp_path)
    assert frozen is not None and frozen.type == "external" and frozen.id == "mine"
