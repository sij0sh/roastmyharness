"""Evaluation registry: identity and freezing for evals and eval types.

An evaluation is the benchmark an experiment runs: bundled DeepSWE,
a generated custom eval, or an external local task set. This module
resolves the spec's ``[evaluation]`` block into a frozen descriptor at
prepare time. Like the benchmark catalog, eval descriptors live beside
task directories (``eval.toml``), never inside them: task content
hashes walk every file under each task dir, so descriptor edits inside
a task dir would fork historic comparability.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roast_my_harness.errors import SpecError
from roast_my_harness.evals.descriptor import eval_descriptor_path, load_descriptor
from roast_my_harness.tasks.catalog import catalog_info

BUNDLED_EVAL_IDS = frozenset({"deepswe"})
"""Eval ids shipped with the harness (more bundled evals later)."""


@dataclass(frozen=True)
class EvalFrozen:
    """Frozen eval identity for one run: what was evaluated, exactly."""

    type: str
    id: str
    revision: str | None
    eval_hash: str | None


def resolve_eval(
    spec: Any,
    task_root: Path,
    *,
    catalog_revision: str | None = None,
    catalog_hash: str | None = None,
) -> EvalFrozen | None:
    """Freeze the spec's eval selection; None for the legacy default.

    A missing ``[evaluation]`` block — or an explicit block that states
    only the default (bundled DeepSWE, no revision) — resolves to None
    so existing runs keep byte-identical identity and historic-control
    cohort keys. Anything else freezes to an EvalFrozen whose fields
    enter run identity. Raises SpecError for unknown bundled ids,
    missing generated descriptors, and id mismatches.
    """
    selection = spec.evaluation
    if selection is None:
        return None
    eval_type = selection.type
    eval_id = selection.id or "deepswe"
    if eval_type == "bundled" and eval_id == "deepswe" and selection.revision is None:
        return None
    if eval_type == "bundled":
        if eval_id not in BUNDLED_EVAL_IDS:
            raise SpecError(
                f"unknown bundled eval {eval_id!r} "
                f"(available: {', '.join(sorted(BUNDLED_EVAL_IDS)) or 'none'})"
            )
        if catalog_revision is None or catalog_hash is None:
            catalog_revision, catalog_hash = catalog_info(task_root)
        return EvalFrozen(
            type="bundled",
            id=eval_id,
            revision=selection.revision or catalog_revision,
            eval_hash=catalog_hash,
        )
    path = eval_descriptor_path(task_root)
    descriptor = load_descriptor(task_root)
    if descriptor is None:
        if eval_type == "generated":
            raise SpecError(
                f"evaluation type 'generated' needs an eval descriptor at "
                f"{path}, none found"
            )
        return EvalFrozen(
            type="external", id=eval_id, revision=selection.revision, eval_hash=None
        )
    if descriptor.id != eval_id:
        raise SpecError(
            f"eval descriptor {path} describes id {descriptor.id!r}, "
            f"but the spec selects eval {eval_id!r}"
        )
    file_revision = descriptor.revision
    if selection.revision is not None and file_revision is not None:
        if selection.revision != file_revision:
            raise SpecError(
                f"evaluation revision {selection.revision!r} conflicts with "
                f"eval descriptor revision {file_revision!r} in {path}"
            )
    return EvalFrozen(
        type=eval_type,
        id=eval_id,
        revision=selection.revision or file_revision,
        eval_hash=descriptor.sha256,
    )


def cohort_eval_id(spec: Any) -> str | None:
    """Eval component for historic-control cohort keys; None is legacy.

    Only non-default evals scope the cohort: the legacy default and an
    explicit bundled-DeepSWE selection share one key so existing history
    stays eligible, while every other eval gets its own cohort.
    """
    frozen = resolve_eval(spec, spec.tasks.path)
    if frozen is None:
        return None
    return f"{frozen.type}:{frozen.id}:{frozen.revision}"


def eval_label(spec: Any) -> str:
    """Human-stable eval label for reviews and summaries: type/id."""
    selection = spec.evaluation
    if selection is None:
        return "bundled/deepswe"
    return f"{selection.type}/{selection.id or 'deepswe'}"
