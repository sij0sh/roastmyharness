"""Pure planning and drift checks for historic control reuse."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

AVAILABILITY_STATES = ("unavailable", "partial", "eligible")
ACCEPTANCE_STATES = ("checking_drift", "accepted", "rejected_drift", "inconclusive")


@dataclass(frozen=True)
class ReuseDecision:
    status: str  # availability: unavailable | partial | eligible
    eligible_tasks: list[str]
    control_tasks: list[str]
    reuse_by_task: dict[str, bool]
    pool_counts: dict[str, int]
    pool_date_ranges: dict[str, tuple[str, str]]
    sentinel_tasks: list[str] = field(default_factory=list)


def _get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return getattr(row, key, None)


def observations_within_age(
    rows: list[Any], maximum_age_days: int, now: datetime | None = None
) -> list[Any]:
    now = now or datetime.now(UTC)
    kept: list[Any] = []
    for row in rows:
        try:
            observed = datetime.fromisoformat(
                str(_get(row, "observed_at")).replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            continue
        if (now - observed).days <= maximum_age_days:
            kept.append(row)
    return kept


def plan_reuse(
    *,
    mode: str,
    scope: str,
    selected: list[str],
    pools: dict[str, list[Any]],
    minimum_runs: int,
    maximum_age_days: int,
    sentinel_count: int,
    seed: int,
) -> ReuseDecision:
    """Deterministic historic plan over the selected tasks.

    mode fresh reuses nothing; mode historic holds eligible non-sentinel
    tasks for history. scope intersection runs the control arm only on
    history-backed tasks; hybrid runs fresh controls for the rest.
    Sentinels sample from eligible tasks only, never the full set.
    """
    counts: dict[str, int] = {}
    ranges: dict[str, tuple[str, str]] = {}
    eligible: list[str] = []
    for task_id in selected:
        aged = observations_within_age(
            [row for row in pools.get(task_id, []) if _get(row, "resolved") is not None],
            maximum_age_days,
        )
        dates = sorted(
            str(_get(row, "observed_at"))
            for row in aged
            if _get(row, "observed_at")
        )
        counts[task_id] = len(aged) if mode == "historic" else 0
        ranges[task_id] = (
            (dates[0], dates[-1]) if dates and mode == "historic" else ("", "")
        )
        if mode == "historic" and len(aged) >= minimum_runs:
            eligible.append(task_id)
    sentinels = (
        sentinel_sample(eligible, sentinel_count, seed) if mode == "historic" else []
    )
    if mode == "historic":
        if not eligible:
            status = "unavailable"
        elif len(eligible) == len(selected):
            status = "eligible"
        else:
            status = "partial"
    if mode == "fresh":
        status = "eligible"
        control_tasks = list(selected)
    elif scope == "intersection":
        control_tasks = list(eligible)
    else:
        control_tasks = list(selected)
    reuse = {
        task_id: mode == "historic"
        and task_id in eligible
        and task_id not in sentinels
        for task_id in selected
    }
    return ReuseDecision(
        status=status,
        eligible_tasks=eligible,
        control_tasks=control_tasks,
        reuse_by_task=reuse,
        pool_counts=counts,
        pool_date_ranges=ranges,
        sentinel_tasks=sentinels,
    )


def acceptance_state(
    *,
    verdict: dict[str, Any],
    on_drift: str,
    on_inconclusive: str,
) -> tuple[str, bool, bool]:
    """(status, accepted, abort) from a sentinel verdict plus policy.

    rejected drift and inconclusive samples release held tasks to fresh
    runs unless the policy aborts the run instead. No hidden fallback:
    the stored on_drift/on_inconclusive decides.
    """
    if verdict.get("reject"):
        return "rejected_drift", False, on_drift == "abort"
    if not verdict.get("informative"):
        return "inconclusive", False, on_inconclusive == "abort"
    return "accepted", True, False


def sentinel_sample(task_ids: list[str], count: int, seed: int) -> list[str]:
    if count <= 0 or not task_ids:
        return []
    ordered = sorted(task_ids)
    step = max(1, len(ordered) // count)
    picked: list[str] = []
    index = seed % len(ordered)
    while len(picked) < min(count, len(ordered)):
        candidate = ordered[index % len(ordered)]
        if candidate not in picked:
            picked.append(candidate)
        index += step
    return picked


def sentinel_verdict(
    *,
    fresh: list[tuple[str, bool]],
    historic: dict[str, list[bool]],
    p_threshold: float = 0.05,
) -> dict[str, Any]:
    probabilities: list[float] = []
    discordant: list[dict[str, Any]] = []
    observed = 0
    for task_id, passed in fresh:
        history = historic.get(task_id, [])
        if not history:
            continue
        rate = sum(history) / len(history)
        majority_pass = rate > 0.5 or (rate == 0.5 and passed)
        probabilities.append((1 - rate) if majority_pass else rate)
        if passed != majority_pass:
            observed += 1
            discordant.append(
                {"task": task_id, "fresh_pass": passed, "historic_rate": rate}
            )
    if not probabilities:
        return {
            "informative": False,
            "p_value": None,
            "reject": False,
            "matches": 0,
            "total": 0,
            "discordant": [],
        }
    p_value = poisson_binomial_upper_tail(probabilities, observed)
    min_p = 1.0
    for probability in probabilities:
        min_p *= probability
    return {
        "informative": len(probabilities) >= 3 and min_p < p_threshold,
        "p_value": p_value,
        "reject": p_value < p_threshold,
        "matches": len(probabilities) - observed,
        "total": len(probabilities),
        "discordant": discordant,
    }


def poisson_binomial_upper_tail(probabilities: list[float], k: int) -> float:
    distribution = [1.0]
    for probability in probabilities:
        following = [0.0] * (len(distribution) + 1)
        for index, mass in enumerate(distribution):
            following[index] += mass * (1 - probability)
            following[index + 1] += mass * probability
        distribution = following
    return sum(distribution[k:])
