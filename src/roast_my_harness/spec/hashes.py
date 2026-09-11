"""Stable hashes over canonical JSON."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from roast_my_harness import ADAPTER_PROTOCOL_VERSION
from roast_my_harness.spec.models import EvalSpec, ExperimentSpec, ModelSpec, VariantSpec

PROMPT_ISOLATION = "pi-fairness-v1"
_DEFAULT_EVALUATION = EvalSpec().model_dump(mode="json")


def is_default_evaluation_dump(value: object) -> bool:
    return value is None or value == _DEFAULT_EVALUATION


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_canonical(obj: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def spec_hash(spec: ExperimentSpec) -> str:
    payload = spec.model_dump(mode="json", exclude={"tasks": {"path"}, "hypothesis": True})
    if is_default_evaluation_dump(payload.get("evaluation")):
        payload.pop("evaluation", None)
    return sha256_canonical(payload)


def resolved_experiment_hash(payload: dict[str, Any]) -> str:
    return sha256_canonical(payload)


def variant_hash(
    variant: VariantSpec,
    pi_version: str,
    source_hashes: dict[str, str] | None = None,
    *,
    agent: str = "pi",
    agent_version: str | None = None,
) -> str:
    variant_data = variant.model_dump(mode="json")
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
