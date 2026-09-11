"""Evaluation support: bundled DeepSWE plus external task roots."""

from roast_my_harness.evals.descriptor import (
    DESCRIPTOR_VERSION,
    EVAL_FILENAME,
    EvalDescriptor,
    eval_descriptor_path,
    load_descriptor,
    parse_descriptor,
)
from roast_my_harness.evals.registry import (
    BUNDLED_EVAL_IDS,
    EvalFrozen,
    eval_label,
    resolve_eval,
)

__all__ = [
    "BUNDLED_EVAL_IDS",
    "DESCRIPTOR_VERSION",
    "EVAL_FILENAME",
    "EvalDescriptor",
    "EvalFrozen",
    "eval_descriptor_path",
    "eval_label",
    "load_descriptor",
    "parse_descriptor",
    "resolve_eval",
]
