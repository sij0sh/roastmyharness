"""Bootstrap determinism and paired-flip counting."""

from __future__ import annotations

import random

import pytest

from roast_my_harness.report.statistics import (
    deterministic_seed,
    paired_flips,
    rate_ci,
    resolved_rows,
)


def test_seed_is_deterministic():
    assert deterministic_seed("exp-1") == deterministic_seed("exp-1")
    assert deterministic_seed("exp-1") != deterministic_seed("exp-2")


def test_rate_ci_deterministic():
    outcomes = [1, 0, 1, 1, 0, 1, 0, 1]
    a = rate_ci(outcomes, random.Random(deterministic_seed("e")))
    b = rate_ci(outcomes, random.Random(deterministic_seed("e")))
    assert a == b
    mean, lo, hi = a
    assert lo <= mean <= hi


def test_duplicate_report_rows_are_rejected():
    rows = [
        {"variant": "a", "task": "t1", "resolved": 1},
        {"variant": "a", "task": "t1", "resolved": 0},
    ]
    with pytest.raises(ValueError, match="duplicate report row"):
        paired_flips(rows)


def test_resolved_rows_excludes_infrastructure_errors():
    rows = [
        {"variant": "a", "task": "t1", "resolved": 0},
        {
            "variant": "a",
            "task": "t2",
            "resolved": 0,
            "exception_type": "AgentTimeoutError",
        },
    ]
    assert [row["task"] for row in resolved_rows(rows)] == ["t1"]


def test_paired_flips_counts():
    rows = [
        {"variant": "a", "task": "t1", "resolved": 1},
        {"variant": "a", "task": "t2", "resolved": 0},
        {"variant": "b", "task": "t1", "resolved": 1},
        {"variant": "b", "task": "t2", "resolved": 1},
    ]
    (va, vb, both, a_fail_b, b_fail_a, discordant) = paired_flips(rows)[0]
    assert (va, vb) == ("a", "b")
    assert (both, a_fail_b, b_fail_a) == (1, 1, 0)
    assert discordant == [("t2", "a fail -> b pass")]
