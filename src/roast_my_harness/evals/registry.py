"""Evaluation registry: bundled DeepSWE plus external task roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roast_my_harness.errors import SpecError
from roast_my_harness.evals.descriptor import eval_descriptor_path, load_descriptor
from roast_my_harness.tasks.catalog import catalog_info

BUNDLED_EVAL_IDS = frozenset({"deepswe"})


@dataclass(frozen=True)
class EvalFrozen:
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
    selection = spec.evaluation
    if selection is None:
        return None
    eval_type = selection.type
    eval_id = selection.id or "deepswe"
    if eval_type == "bundled" and eval_id == "deepswe" and selection.revision is None:
        return None
    if eval_type == "bundled":
        if eval_id not in BUNDLED_EVAL_IDS:
            raise SpecError(f"unknown bundled eval {eval_id!r}")
        if catalog_revision is None or catalog_hash is None:
            catalog_revision, catalog_hash = catalog_info(task_root)
        return EvalFrozen(type="bundled", id=eval_id,
                          revision=selection.revision or catalog_revision,
                          eval_hash=catalog_hash)
    path = eval_descriptor_path(task_root)
    descriptor = load_descriptor(task_root)
    if descriptor is None:
        return EvalFrozen(type="external", id=eval_id,
                          revision=selection.revision, eval_hash=None)
    if descriptor.id != eval_id:
        raise SpecError(f"eval descriptor {path} describes {descriptor.id!r}, "
                          f"spec selects {eval_id!r}")
    file_revision = descriptor.revision
    if selection.revision is not None and file_revision is not None:
        if selection.revision != file_revision:
            raise SpecError(f"evaluation revision {selection.revision!r} "
                              f"conflicts with {file_revision!r}")
    return EvalFrozen(type=eval_type, id=eval_id,
                      revision=selection.revision or file_revision,
                      eval_hash=descriptor.sha256)


def eval_label(spec: Any) -> str:
    selection = spec.evaluation
    if selection is None:
        return "bundled/deepswe"
    return f"{selection.type}/{selection.id or 'deepswe'}"
