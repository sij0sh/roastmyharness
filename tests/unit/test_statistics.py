"""Bootstrap determinism and paired-flip counting."""

from __future__ import annotations

import random

from roast_my_harness.report.statistics import (
    deterministic_seed,
    paired_flips,
    rate_ci,
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
