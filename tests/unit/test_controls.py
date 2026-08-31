"""Unit tests for historic control reuse planning and drift checks."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from roast_my_harness.store.controls import (
    observations_within_age,
    plan_reuse,
    poisson_binomial_upper_tail,
    sentinel_sample,
    sentinel_verdict,
)


def row(days_old: int, resolved=True):
    return SimpleNamespace(
        observed_at=(datetime.now(UTC) - timedelta(days=days_old)).isoformat(),
        resolved=resolved,
    )


def test_observations_within_age_filters_old_and_malformed():
    rows = [row(2), row(40), SimpleNamespace(observed_at="bad", resolved=True)]
    assert observations_within_age(rows, 30) == [rows[0]]


def test_plan_reuse_applies_depth_age_sentinel_and_never():
    pools = {
        "deep": [row(1), row(2), row(3)],
        "shallow": [row(1)],
        "old": [row(40), row(41), row(42)],
        "sentinel": [row(1), row(2), row(3)],
    }
    plan = plan_reuse(
        policy="require",
        pools=pools,
        minimum_runs=3,
        maximum_age_days=30,
        sentinel_tasks=["sentinel"],
    )
    assert plan.reuse_by_task == {
        "deep": True,
        "shallow": False,
        "old": False,
        "sentinel": False,
    }
    assert plan.pool_counts == {"deep": 3, "shallow": 1, "old": 0, "sentinel": 3}
    never = plan_reuse(
        policy="never",
        pools=pools,
        minimum_runs=1,
        maximum_age_days=30,
        sentinel_tasks=[],
    )
    assert not any(never.reuse_by_task.values())
    assert not any(never.pool_counts.values())


def test_sentinel_sample_is_deterministic_and_bounded():
    tasks = [f"t{i}" for i in range(10)]
    assert sentinel_sample(tasks, 3, 42) == sentinel_sample(tasks, 3, 42)
    assert len(sentinel_sample(tasks, 3, 42)) == 3
    assert sentinel_sample(tasks, 0, 42) == []
    assert len(sentinel_sample(tasks[:2], 6, 42)) == 2


def test_sentinel_verdict_accepts_agreement_and_rejects_drift():
    historic = {f"t{i}": [True] * 20 for i in range(6)}
    agree = sentinel_verdict(
        fresh=[(f"t{i}", True) for i in range(6)], historic=historic
    )
    assert agree["reject"] is False
    assert agree["matches"] == 6
    drift = sentinel_verdict(
        fresh=[(f"t{i}", False) for i in range(6)], historic=historic
    )
    assert drift["reject"] is True
    assert drift["p_value"] == 0.0


def test_sentinel_verdict_uninformative_without_history():
    verdict = sentinel_verdict(fresh=[("x", True)], historic={})
    assert verdict == {
        "informative": False,
        "p_value": None,
        "reject": False,
        "matches": 0,
        "total": 0,
        "discordant": [],
    }


def test_poisson_binomial_upper_tail_known_values():
    assert poisson_binomial_upper_tail([0.5, 0.5], 1) == 0.75
    assert poisson_binomial_upper_tail([0.1, 0.2], 2) == pytest.approx(0.02)
