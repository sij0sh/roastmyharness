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


def plan_hybrid(**kwargs):
    args = {
        "mode": "historic",
        "scope": "hybrid",
        "selected": ["deep", "shallow", "old", "sentinel"],
        "minimum_runs": 3,
        "maximum_age_days": 30,
        "sentinel_count": 1,
        "seed": 7,
    }
    args.update(kwargs)
    return plan_reuse(**args)


def test_plan_reuse_applies_depth_age_and_hybrid_scope():
    pools = {
        "deep": [row(1), row(2), row(3)],
        "shallow": [row(1)],
        "old": [row(40), row(41), row(42)],
        "sentinel": [row(1), row(2), row(3)],
    }
    plan = plan_hybrid(pools=pools)
    assert plan.status == "partial"
    assert plan.eligible_tasks == ["deep", "sentinel"]
    assert plan.control_tasks == ["deep", "shallow", "old", "sentinel"]
    assert plan.reuse_by_task[plan.sentinel_tasks[0]] is False
    assert sum(plan.reuse_by_task.values()) == 1
    assert plan.pool_counts == {"deep": 3, "shallow": 1, "old": 0, "sentinel": 3}
    assert len(plan.sentinel_tasks) == 1
    assert plan.sentinel_tasks[0] in ("deep", "sentinel")


def test_plan_reuse_intersection_runs_eligible_only():
    pools = {
        "deep": [row(1), row(2), row(3)],
        "shallow": [row(1)],
    }
    plan = plan_hybrid(pools=pools, scope="intersection", selected=["deep", "shallow"])
    assert plan.status == "partial"
    assert plan.control_tasks == ["deep"]
    assert plan.eligible_tasks == ["deep"]


def test_plan_reuse_unavailable_without_history():
    plan = plan_hybrid(pools={"t1": [row(40)]}, selected=["t1"])
    assert plan.status == "unavailable"
    assert plan.eligible_tasks == []
    assert plan.sentinel_tasks == []


def test_plan_reuse_fresh_mode_runs_everything():
    pools = {"deep": [row(1), row(2), row(3)]}
    plan = plan_hybrid(mode="fresh", pools=pools, selected=["deep"])
    assert plan.status == "eligible"
    assert plan.control_tasks == ["deep"]
    assert not any(plan.reuse_by_task.values())
    assert not any(plan.pool_counts.values())
    assert plan.sentinel_tasks == []


def test_plan_reuse_all_eligible_is_eligible():
    pools = {"a": [row(1), row(2)], "b": [row(1), row(2)]}
    plan = plan_hybrid(
        pools=pools, selected=["a", "b"], minimum_runs=2, sentinel_count=0
    )
    assert plan.status == "eligible"
    assert plan.eligible_tasks == ["a", "b"]
    assert plan.reuse_by_task == {"a": True, "b": True}


def test_acceptance_state_maps_verdict_and_policy():
    from roast_my_harness.store.controls import acceptance_state

    assert acceptance_state(
        verdict={"reject": True, "informative": True},
        on_drift="fresh",
        on_inconclusive="fresh",
    ) == ("rejected_drift", False, False)
    assert acceptance_state(
        verdict={"reject": True, "informative": True},
        on_drift="abort",
        on_inconclusive="fresh",
    ) == ("rejected_drift", False, True)
    assert acceptance_state(
        verdict={"reject": False, "informative": False},
        on_drift="fresh",
        on_inconclusive="abort",
    ) == ("inconclusive", False, True)
    assert acceptance_state(
        verdict={"reject": False, "informative": True},
        on_drift="abort",
        on_inconclusive="abort",
    ) == ("accepted", True, False)


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
