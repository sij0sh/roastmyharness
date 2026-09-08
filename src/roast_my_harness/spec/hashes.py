"""Stable hashes over canonical JSON. SHA-256, sorted keys, UTF-8, no spaces.

Content-vs-policy rule (owner: spec; approved, gated): home identity
covers content fields only; run-policy fields travel the runner channel
and stay out of the hash/manifest. Enforcement waits for the second
runtime-policy field; until then variant_hash keeps its current input.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from roast_my_harness import ADAPTER_PROTOCOL_VERSION
from roast_my_harness.spec.models import EvalSpec, ExperimentSpec, ModelSpec, VariantSpec

PROMPT_ISOLATION = "pi-fairness-v1"

_DEFAULT_EVALUATION = EvalSpec().model_dump(mode="json")


def is_default_evaluation_dump(value: object) -> bool:
    """True for absent or all-default [evaluation] payloads (legacy identity)."""
    return value is None or value == _DEFAULT_EVALUATION


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_canonical(obj: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def spec_hash(spec: ExperimentSpec) -> str:
    # hypothesis is a frozen annotation, not execution config: runs that
    # differ only in prose must keep one identity so history stays joined.
    # A missing [evaluation] block likewise keeps legacy identity: it pops
    # from the payload so pre-eval runs and default-eval runs hash alike.
    payload = spec.model_dump(
        mode="json", exclude={"tasks": {"path"}, "hypothesis": True}
    )
    if is_default_evaluation_dump(payload.get("evaluation")):
        payload.pop("evaluation", None)
    return sha256_canonical(payload)


def resolved_experiment_hash(payload: dict[str, Any]) -> str:
    """Identity of a run over frozen resolved content.

    payload is a ResolvedRunSpec dump minus run_id (excluding the derived
    id keeps the hash non-circular). It covers the requested config, the
    exact resolved agent versions, and the ordered task id/content-hash
    map, so a moved ``latest`` pin yields a new run instead of silently
    reusing old cells. Callers must build it via resolve_run_spec, never
    by hand, so every hashed field passed through the single freeze point.
    """
    return sha256_canonical(payload)


def variant_hash(
    variant: VariantSpec,
    pi_version: str,
    source_hashes: dict[str, str] | None = None,
    *,
    agent: str = "pi",
    agent_version: str | None = None,
) -> str:
    """Hash of the normalized variant, its copied sources, and pin versions.

    source_hashes maps a stable source key (extension/skill name) to the
    content hash of the tree that will be copied into the home.

    agent and agent_version key the cache by resolved agent identity so
    cached homes never mix agents or versions.

    Literal env values never enter the hash input: cached homes do not
    contain them (values are staged per run), so homes differing only in
    env values are safely shared. Env names and env_from_host names are
    covered so structural changes still re-key the cache.
    """
    variant_data = variant.model_dump(mode="json", exclude={"env"})
    return sha256_canonical(
        {
            "variant": variant_data,
            "env_names": sorted(variant.env),
            "env_from_host": sorted(variant.env_from_host),
            "sources": source_hashes or {},
            "pi_version": pi_version,
            "agent": agent,
            "agent_version": agent_version,
            "adapter_protocol": ADAPTER_PROTOCOL_VERSION,
        }
    )


def control_cohort_key(
    control_hash: str,
    model: ModelSpec,
    thinking: str,
    task_hash: str,
    *,
    agent: str,
    agent_version: str,
    eval_id: str | None = None,
) -> str:
    """Identity of comparable control observations for one task.

    eval_id scopes the cohort to one evaluation; None is the legacy
    default, so pre-eval history stays eligible for default-eval runs
    while every other eval gets its own cohort.
    """
    payload: dict[str, object] = {
        "control_hash": control_hash,
        "agent": agent,
        "agent_version": agent_version,
        "provider": model.provider,
        "provider_id": model.provider_id,
        "model_id": model.id,
        "resolved_model": model.resolved_model.model_dump(mode="json")
        if model.resolved_model
        else None,
        "thinking": thinking,
        "adapter_protocol": ADAPTER_PROTOCOL_VERSION,
        "task_hash": task_hash,
        "prompt_isolation": PROMPT_ISOLATION,
    }
    if eval_id is not None:
        payload["eval_id"] = eval_id
    return sha256_canonical(payload)
