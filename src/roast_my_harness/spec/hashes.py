"""Stable hashes over canonical JSON. SHA-256, sorted keys, UTF-8, no spaces."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from roast_my_harness import ADAPTER_PROTOCOL_VERSION
from roast_my_harness.spec.models import ExperimentSpec, ModelSpec, VariantSpec

# The control cohort key includes prompt isolation; it never changes in V1.
PROMPT_ISOLATION = "pi-fairness-v1"


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_canonical(obj: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def spec_hash(spec: ExperimentSpec) -> str:
    return sha256_canonical(spec.model_dump(mode="json", exclude={"tasks": {"path"}}))


def experiment_hash(spec: ExperimentSpec, task_hashes: list[tuple[str, str]]) -> str:
    """Identity of a run: config plus the ordered task id/content-hash map.

    task_hashes is the discovered task list in stable order, each entry
    (task_id, content hash). Binding content into identity means changing
    task files under unchanged ids yields a new experiment instead of
    silently reusing old cells.
    """
    return sha256_canonical(
        {
            "spec": spec.model_dump(mode="json", exclude={"tasks": {"path"}}),
            "tasks": [[task_id, content_hash] for task_id, content_hash in task_hashes],
        }
    )


def variant_hash(
    variant: VariantSpec,
    pi_version: str,
    source_hashes: dict[str, str] | None = None,
) -> str:
    """Hash of the normalized variant, its copied sources, and pin versions.

    source_hashes maps a stable source key (extension/skill name) to the
    content hash of the tree that will be copied into the home.

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
            "adapter_protocol": ADAPTER_PROTOCOL_VERSION,
        }
    )


def control_cohort_key(
    control_hash: str,
    model: ModelSpec,
    thinking: str,
    pi_version: str,
    task_hash: str,
) -> str:
    return sha256_canonical(
        {
            "control_hash": control_hash,
            "provider": model.provider,
            "provider_id": model.provider_id,
            "model_id": model.id,
            "thinking": thinking,
            "pi_version": pi_version,
            "adapter_protocol": ADAPTER_PROTOCOL_VERSION,
            "task_hash": task_hash,
            "prompt_isolation": PROMPT_ISOLATION,
        }
    )
