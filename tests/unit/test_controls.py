"""Historic control reuse planning (plan section 16)."""

from __future__ import annotations

from types import SimpleNamespace

from roast_my_harness.store.controls import (
    observations_within_age,
    plan_reuse,
    sentinel_sample,
    sentinel_verdict,
)


def obs(resolved: int, observed_at: str) -> SimpleNamespace:
    return SimpleNamespace(resolved=resolved, observed_at=observed_at)


def test_plan_reuse_never_disables_everything():
    pools = {"t1": [obs(1, "2026-08-20T00:00:00Z")] * 12}
    decision = plan_reuse(
        policy="never", pools=pools, minimum_runs=10,
        maximum_age_days=30, sentinel_tasks=[],
    )
    assert decision.reuse_by_task == {"t1": False}
    assert decision.pool_counts["t1"] == 0


def test_plan_reuse_requires_minimum_runs():
    pools = {
        "t1": [obs(1, "2026-08-20T00:00:00Z")] * 12,
        "t2": [obs(1, "2026-08-20T00:00:00Z")] * 3,
    }
    decision = plan_reuse(
        policy="ask", pools=pools, minimum_runs=10,
        maximum_age_days=30, sentinel_tasks=[],
    )
    assert decision.reuse_by_task["t1"] is True
    assert decision.reuse_by_task["t2"] is False


def test_plan_reuse_respects_age_limit():
    old = [obs(1, "2026-01-01T00:00:00Z")] * 12
    fresh = [obs(1, "2026-08-20T00:00:00Z")] * 12
    decision = plan_reuse(
        policy="ask", pools={"old": old, "fresh": fresh},
        minimum_runs=10, maximum_age_days=30, sentinel_tasks=[],
    )
    assert decision.reuse_by_task["old"] is False
    assert decision.reuse_by_task["fresh"] is True


def test_plan_reuse_sentinels_always_fresh():
    pools = {"t1": [obs(1, "2026-08-20T00:00:00Z")] * 12}
    decision = plan_reuse(
        policy="ask", pools=pools, minimum_runs=10,
        maximum_age_days=30, sentinel_tasks=["t1"],
    )
    assert decision.reuse_by_task["t1"] is False


def test_observations_within_age_drops_old_rows():
    from datetime import UTC, datetime

    now = datetime(2026, 8, 26, tzinfo=UTC)
    rows = [
        obs(1, "2026-08-25T00:00:00+00:00"),  
        obs(0, "2026-01-01T00:00:00+00:00"),  
        obs(1, "not-a-date"),                 
    ]
    kept = observations_within_age(rows, 30, now=now)
    assert [r.resolved for r in kept] == [1]


def test_sentinel_sample_deterministic_and_bounded():
    tasks = [f"t{i}" for i in range(20)]
    a = sentinel_sample(tasks, 4, seed=123)
    b = sentinel_sample(tasks, 4, seed=123)
    assert a == b
    assert len(a) == 4
    assert set(a) <= set(tasks)
    assert sentinel_sample(tasks, 0, 1) == []



def test_sentinel_verdict_rejects_full_disagreement():
    
    historic = {f"t{i}": [True] * 5 for i in range(6)}
    fresh = [(f"t{i}", False) for i in range(6)]
    verdict = sentinel_verdict(fresh=fresh, historic=historic)
    assert verdict["matches"] == 0
    assert verdict["total"] == 6
    assert verdict["p_value"] < 0.05
    assert verdict["reject"] is True
    assert verdict["informative"] is True


def test_sentinel_verdict_passes_agreement():
    historic = {"t1": [True] * 5, "t2": [True] * 5}
    fresh = [("t1", True), ("t2", True)]
    verdict = sentinel_verdict(fresh=fresh, historic=historic)
    assert verdict["reject"] is False


def test_sentinel_verdict_uninformative_small_sample():
    
    
    historic = {f"t{i}": [True, False] * 3 for i in range(4)}  
    fresh = [(f"t{i}", False) for i in range(4)]
    verdict = sentinel_verdict(fresh=fresh, historic=historic)
    assert verdict["informative"] is False  


def test_sentinel_verdict_unanimous_history_single_flip_rejects():
    
    historic = {f"t{i}": [True] * 5 for i in range(6)}
    fresh = [(f"t{i}", i != 2) for i in range(6)]  
    verdict = sentinel_verdict(fresh=fresh, historic=historic)
    assert verdict["total"] == 6
    assert verdict["p_value"] == 0.0
    assert verdict["reject"] is True


def test_sentinel_verdict_informative_when_rejection_possible():
    
    historic = {f"t{i}": [True] * 5 for i in range(6)}
    fresh_all_agree = [(f"t{i}", True) for i in range(6)]
    verdict = sentinel_verdict(fresh=fresh_all_agree, historic=historic)
    assert verdict["informative"] is True
    assert verdict["reject"] is False
