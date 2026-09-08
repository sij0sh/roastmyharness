"""Frozen run identity: versions and task content fixed at prepare time.

User TOML may pin ``latest``; at prepare time every agent pin is resolved
to an exact version exactly once. The resulting ResolvedRunSpec is the
sole input to run identity (run id), home cache keys, historic-cohort
keys, the launch plan, and the report manifest. Nothing re-resolves after
approval: resume reloads the frozen spec from the run dir instead.

Extension points for later v2 phases (defaults keep v1 semantics):
repetitions (phase 2: rollouts), catalog_revision/catalog_hash (phase 3:
benchmark catalog), eval_* (evals: frozen evaluation identity).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from roast_my_harness.evals.registry import EvalFrozen
from roast_my_harness.spec.hashes import is_default_evaluation_dump, resolved_experiment_hash
from roast_my_harness.spec.models import ExperimentSpec
from roast_my_harness.spec.normalize import experiment_id as make_experiment_id

RESOLVED_SCHEMA_VERSION = 1
"""Version of this frozen envelope (not the TOML schema_version).

Additive optional fields (repetitions, catalog_*, eval_*) do not bump
this: old envelopes validate with the new fields defaulting to None,
and identity_payload keeps legacy-default runs byte-identical.
"""


class ResolvedRunSpec(BaseModel):
    """Immutable run identity, fixed at prepare and reloaded on resume."""

    model_config = ConfigDict(extra="forbid")

    resolved_schema_version: int = RESOLVED_SCHEMA_VERSION
    experiment_name: str
    requested_spec: dict[str, Any]
    requested_agent_versions: dict[str, str]
    resolved_agent_versions: dict[str, str]
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
    """Hash input for a frozen run: everything identity-bearing, nothing else.

    Legacy-default evals (all eval_* None) pop from the payload so
    pre-eval runs and default-eval runs keep byte-identical identity.
    Callers must use this helper — never hand-built dicts — so every
    hashed field passed through the single freeze point.
    """
    payload = resolved.model_dump(
        mode="json",
        exclude={"run_id": True, "requested_spec": {"hypothesis"}},
    )
    if (
        payload.get("eval_type") is None
        and payload.get("eval_id") is None
        and payload.get("eval_revision") is None
        and payload.get("eval_hash") is None
    ):
        for key in ("eval_type", "eval_id", "eval_revision", "eval_hash"):
            payload.pop(key, None)
    requested = payload.get("requested_spec")
    if isinstance(requested, dict) and is_default_evaluation_dump(
        requested.get("evaluation")
    ):
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
    """Freeze one run: resolve pins, bind task content, derive the run id.

    eval is the frozen evaluation from evals.resolve_eval (None for the
    legacy default). Raises RuntimeError when a ``latest`` pin cannot be
    resolved (npm missing or registry unreachable); callers surface that
    as a preparation failure, never a silent fallback.
    """
    agents = sorted(set(spec.resolved_agents().values()))
    requested = {agent: spec.agent_version_for(agent) for agent in agents}
    resolved_versions = {agent: spec.resolved_version_for(agent) for agent in agents}
    resolved = ResolvedRunSpec(
        experiment_name=spec.name,
        requested_spec=spec.model_dump(mode="json", exclude={"tasks": {"path"}}),
        requested_agent_versions=requested,
        resolved_agent_versions=resolved_versions,
        tasks=list(task_pairs),
        catalog_revision=catalog_revision,
        catalog_hash=catalog_hash,
        repetitions=repetitions,
        eval_type=eval.type if eval is not None else None,
        eval_id=eval.id if eval is not None else None,
        eval_revision=eval.revision if eval is not None else None,
        eval_hash=eval.eval_hash if eval is not None else None,
    )
    # hypothesis is a frozen annotation, not execution config: runs that
    # differ only in prose keep one run id so history stays joined.
    digest = resolved_experiment_hash(identity_payload(resolved))
    return resolved.model_copy(update={"run_id": make_experiment_id(spec.name, digest)})
