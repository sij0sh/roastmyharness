"""Frozen run identity fixed at prepare time."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from roast_my_harness.evals.registry import EvalFrozen
from roast_my_harness.spec.hashes import is_default_evaluation_dump, resolved_experiment_hash
from roast_my_harness.spec.models import ExperimentSpec
from roast_my_harness.spec.normalize import experiment_id as make_experiment_id

RESOLVED_SCHEMA_VERSION = 2


class ResolvedRunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolved_schema_version: int = RESOLVED_SCHEMA_VERSION
    experiment_name: str
    requested_spec: dict[str, Any]
    requested_pi_versions: dict[str, str]
    resolved_pi_versions: dict[str, str]
    tasks: list[tuple[str, str]]
    catalog_revision: str | None = None
    catalog_hash: str | None = None
    repetitions: int = 1
    eval_type: str | None = None
    eval_id: str | None = None
    eval_revision: str | None = None
    eval_hash: str | None = None
    run_id: str = ""


def identity_payload(resolved: ResolvedRunSpec) -> dict[str, Any]:
    exclude = {"run_id": True, "requested_spec": {"hypothesis"}}
    payload = resolved.model_dump(mode="json", exclude=exclude)
    if all(payload.get(k) is None for k in ("eval_type", "eval_id", "eval_revision", "eval_hash")):
        for key in ("eval_type", "eval_id", "eval_revision", "eval_hash"):
            payload.pop(key, None)
    requested = payload.get("requested_spec")
    if isinstance(requested, dict) and is_default_evaluation_dump(requested.get("evaluation")):
        requested.pop("evaluation", None)
    return payload


def resolve_run_spec(
    spec: ExperimentSpec,
    task_pairs: list[tuple[str, str]],
    *,
    repetitions: int = 1,
    catalog_revision: str | None = None,
    catalog_hash: str | None = None,
    eval: EvalFrozen | None = None,
) -> ResolvedRunSpec:
    requested = {"pi": spec.pi_version}
    for variant in spec.arms():
        pin = spec.pi_version_for(variant if variant.id != "control" else None)
        requested[f"pi:{variant.id}"] = pin
    resolved_versions = {}
    for key in requested:
        variant_id = key.split(":", 1)[1] if ":" in key else None
        variant = next((v for v in spec.arms() if v.id == variant_id), None)
        resolved_versions[key] = spec.resolved_pi_version_for(variant)
    resolved = ResolvedRunSpec(
        experiment_name=spec.name,
        requested_spec=spec.model_dump(mode="json", exclude={"tasks": {"path"}}),
        requested_pi_versions=requested,
        resolved_pi_versions=resolved_versions,
        tasks=list(task_pairs),
        catalog_revision=catalog_revision,
        catalog_hash=catalog_hash,
        repetitions=repetitions,
        eval_type=eval.type if eval is not None else None,
        eval_id=eval.id if eval is not None else None,
        eval_revision=eval.revision if eval is not None else None,
        eval_hash=eval.eval_hash if eval is not None else None,
    )
    digest = resolved_experiment_hash(identity_payload(resolved))
    return resolved.model_copy(update={"run_id": make_experiment_id(spec.name, digest)})
