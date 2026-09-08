"""Eval descriptor: the frozen contract beside a custom eval's tasks.

``eval.toml`` lives beside the task root (never inside a task dir, so
descriptor edits never fork task content hashes). It names the eval,
states the scoring bar, and pins the judge contract when the eval uses
one. Verifiers fold dimensions into the scalar ``reward`` per this
contract; the harness grades the scalar and reports dimensions
separately. Strict parsing is deliberate: a frozen benchmark must not
carry ambiguous fields.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from roast_my_harness.errors import SpecError

EVAL_FILENAME = "eval.toml"
"""Eval descriptor filename, resolved beside the task root."""


def eval_descriptor_path(task_root: Path) -> Path:
    """Location of the eval descriptor beside a task root."""
    return Path(task_root) / EVAL_FILENAME

DESCRIPTOR_VERSION = 1
"""eval.toml schema version."""

_TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "id", "revision", "title", "description", "scoring", "judge"}
)
_SCORING_KEYS = frozenset({"pass_threshold"})
_JUDGE_KEYS = frozenset({"enabled", "model", "rubric", "samples"})


@dataclass(frozen=True)
class EvalDescriptor:
    """Validated eval.toml content plus its content hash."""

    id: str
    revision: str | None
    title: str
    description: str
    pass_threshold: float
    judge_enabled: bool
    judge_model: str | None
    judge_rubric: str | None
    judge_samples: int
    sha256: str


def _fail(path: Path, message: str) -> SpecError:
    return SpecError(f"invalid eval descriptor {path}: {message}")


def parse_descriptor(path: Path, raw: dict[str, Any]) -> EvalDescriptor:
    """Validate parsed eval.toml content; raises SpecError."""
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise _fail(path, f"unknown fields: {sorted(unknown)}")
    version = raw.get("schema_version", DESCRIPTOR_VERSION)
    if version != DESCRIPTOR_VERSION:
        raise _fail(
            path,
            f"unsupported schema_version {version!r}, expected {DESCRIPTOR_VERSION}",
        )
    eval_id = raw.get("id")
    if not eval_id or not isinstance(eval_id, str):
        raise _fail(path, "id is required and must be a string")
    revision = raw.get("revision")
    if revision is not None and not isinstance(revision, str):
        raise _fail(path, "revision must be a string")
    scoring = raw.get("scoring")
    if not isinstance(scoring, dict):
        raise _fail(path, "[scoring] is required")
    unknown_scoring = set(scoring) - _SCORING_KEYS
    if unknown_scoring:
        raise _fail(path, f"unknown [scoring] fields: {sorted(unknown_scoring)}")
    threshold = scoring.get("pass_threshold")
    if (
        not isinstance(threshold, (int, float))
        or isinstance(threshold, bool)
        or not 0.0 < float(threshold) <= 1.0
    ):
        raise _fail(path, "[scoring] pass_threshold is required (0 < t <= 1)")
    judge = raw.get("judge", {})
    if not isinstance(judge, dict):
        raise _fail(path, "[judge] must be a mapping")
    unknown_judge = set(judge) - _JUDGE_KEYS
    if unknown_judge:
        raise _fail(path, f"unknown [judge] fields: {sorted(unknown_judge)}")
    enabled = judge.get("enabled", False)
    if not isinstance(enabled, bool):
        raise _fail(path, "[judge] enabled must be true/false")
    model = judge.get("model")
    rubric = judge.get("rubric")
    samples = judge.get("samples", 1)
    if enabled:
        if not model or not isinstance(model, str):
            raise _fail(path, "[judge] model is required when enabled")
        if not rubric or not isinstance(rubric, str):
            raise _fail(path, "[judge] rubric is required when enabled")
        if not isinstance(samples, int) or samples < 1:
            raise _fail(path, "[judge] samples must be an integer >= 1")
    elif model is not None or rubric is not None or "samples" in judge:
        raise _fail(
            path,
            "[judge] model/rubric/samples need enabled = true; "
            "a disabled judge pins nothing",
        )
    return EvalDescriptor(
        id=str(eval_id),
        revision=revision,
        title=str(raw.get("title", "")),
        description=str(raw.get("description", "")),
        pass_threshold=float(threshold),
        judge_enabled=enabled,
        judge_model=str(model) if model is not None else None,
        judge_rubric=str(rubric) if rubric is not None else None,
        judge_samples=int(samples),
        sha256="",
    )


def load_descriptor(task_root: Path) -> EvalDescriptor | None:
    """Parse and validate eval.toml beside a task root; None when absent."""
    path = eval_descriptor_path(task_root)
    if not path.is_file():
        return None
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SpecError(f"cannot read eval descriptor {path}: {e}") from e
    if not isinstance(raw, dict):
        raise SpecError(f"invalid eval descriptor {path}: expected a mapping")
    parsed = parse_descriptor(path, raw)
    return replace(
        parsed, sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )
