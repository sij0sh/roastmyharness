"""Evaluation support: eval identity, descriptors, and builders."""

from roast_my_harness.evals.builder import (
    WorkspaceReport,
    safe_join,
    validate_workspace,
    write_text_sandboxed,
)
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
    cohort_eval_id,
    eval_label,
    resolve_eval,
)
from roast_my_harness.evals.selftest import (
    SELFTEST_FILENAME,
    VALIDATION_DIRNAME,
    SelfTestFailure,
    SelfTestResult,
    evaluate_fixtures,
    load_fixtures,
    run_selftests,
    selftest_path,
)

__all__ = [
    "BUNDLED_EVAL_IDS",
    "WorkspaceReport",
    "DESCRIPTOR_VERSION",
    "EVAL_FILENAME",
    "SELFTEST_FILENAME",
    "VALIDATION_DIRNAME",
    "EvalDescriptor",
    "EvalFrozen",
    "SelfTestFailure",
    "SelfTestResult",
    "cohort_eval_id",
    "eval_descriptor_path",
    "eval_label",
    "evaluate_fixtures",
    "load_descriptor",
    "load_fixtures",
    "parse_descriptor",
    "resolve_eval",
    "run_selftests",
    "safe_join",
    "selftest_path",
    "validate_workspace",
    "write_text_sandboxed",
]
