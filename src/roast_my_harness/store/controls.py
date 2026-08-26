"""Historic control reuse planning (plan section 16).

Pure logic over the observation pool: no IO, no clock. The controller
feeds cohorts and pools in; this module decides what runs fresh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ReuseDecision:
    """Per-task plan plus everything the report must disclose."""

    reuse_by_task: dict[str, bool]
    pool_counts: dict[str, int]
    pool_date_ranges: dict[str, tuple[str, str]]
    sentinel_tasks: list[str] = field(default_factory=list)


def _get(row: Any, key: str) -> Any:
    """Field access for sqlite3.Row and SimpleNamespace alike."""
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return getattr(row, key, None)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def observations_within_age(
    rows: list[Any], maximum_age_days: int, now: datetime | None = None
) -> list[Any]:
    """Drop observations older than maximum_age_days (inclusive of errors)."""
    now = now or datetime.now(UTC)
    kept: list[Any] = []
    for row in rows:
        try:
            observed = _parse_iso(str(_get(row, "observed_at")))
        except (ValueError, TypeError):
            continue  
        if (now - observed).days <= maximum_age_days:
            kept.append(row)
    return kept


def plan_reuse(
    *,
    policy: str,
    pools: dict[str, list[Any]],
    minimum_runs: int,
    maximum_age_days: int,
    sentinel_tasks: list[str],
) -> ReuseDecision:
    """Decide per task whether the eligible pool satisfies reuse.

    A task reuses history only when its age-filtered pool has at least
    minimum_runs terminal (non-error) observations. Sentinel tasks always
    run fresh regardless of pool depth (they gate the reuse decision).
    Policy 'never' disables reuse entirely.
    """
    reuse_by_task: dict[str, bool] = {}
    pool_counts: dict[str, int] = {}
    pool_date_ranges: dict[str, tuple[str, str]] = {}

    if policy == "never":
        for task_hash in pools:
            reuse_by_task[task_hash] = False
            pool_counts[task_hash] = 0
            pool_date_ranges[task_hash] = ("", "")
        return ReuseDecision(reuse_by_task, pool_counts, pool_date_ranges,
                             list(sentinel_tasks))

    for task_hash, rows in pools.items():
        terminal = [r for r in rows if _get(r, "resolved") is not None]
        aged = observations_within_age(terminal, maximum_age_days)
        dates = sorted(str(_get(r, "observed_at")) for r in aged if _get(r, "observed_at"))
        pool_counts[task_hash] = len(aged)
        pool_date_ranges[task_hash] = (
            (dates[0], dates[-1]) if dates else ("", "")
        )
        reuse_by_task[task_hash] = (
            task_hash not in sentinel_tasks and len(aged) >= minimum_runs
        )
    return ReuseDecision(reuse_by_task, pool_counts, pool_date_ranges,
                         list(sentinel_tasks))


def sentinel_sample(
    task_hashes: list[str], count: int, seed: int
) -> list[str]:
    """Deterministic sentinel sample keyed by the spec hash seed."""
    if count <= 0 or not task_hashes:
        return []
    ordered = sorted(task_hashes)
    step = max(1, len(ordered) // count)
    picked: list[str] = []
    i = seed % len(ordered)
    while len(picked) < min(count, len(ordered)):
        candidate = ordered[i % len(ordered)]
        if candidate not in picked:
            picked.append(candidate)
        i += step if step > 1 else 1
        if len(picked) >= len(ordered):
            break
    return picked


def binomial_two_sided(k: int, n: int, p: float = 0.5) -> float:
    """Exact two-sided binomial p-value (small n only; sum the tail)."""
    if n <= 0:
        return 1.0

    def pmf(successes: int) -> float:
        coeff = 1.0
        for j in range(successes):
            coeff = coeff * (n - j) / (j + 1)
        return coeff * (p**successes) * ((1 - p) ** (n - successes))

    observed = pmf(k)
    return min(1.0, sum(pmf(i) for i in range(n + 1) if pmf(i) <= observed + 1e-12))


def sentinel_verdict(
    *,
    fresh: list[tuple[str, bool]],
    historic: dict[str, list[bool]],
    p_threshold: float = 0.05,
) -> dict[str, Any]:
    """Gate reuse on fresh sentinel outcomes vs pooled historic rates.

    Null: each fresh outcome is a Bernoulli draw at the task's historic
    pass rate. Statistic: number of tasks disagreeing with the historic
    majority. P-value: exact Poisson-binomial upper tail. Only excess
    discordance rejects; perfect agreement never does.
    """
    qs: list[float] = []
    discordant: list[dict[str, Any]] = []
    observed_discordance = 0
    for task_hash, passed in fresh:
        history = historic.get(task_hash, [])
        if not history:
            continue  # no comparable history for this task
        hist_rate = sum(history) / len(history)
        majority_pass = hist_rate > 0.5 or (hist_rate == 0.5 and passed)
        q = (1 - hist_rate) if majority_pass else hist_rate
        qs.append(q)
        disagrees = passed != majority_pass
        if disagrees:
            observed_discordance += 1
            discordant.append(
                {
                    "task": task_hash,
                    "fresh_pass": passed,
                    "historic_rate": hist_rate,
                }
            )
    n = len(qs)
    if n == 0:
        return {"informative": False, "p_value": None, "reject": False,
                "matches": 0, "total": 0, "discordant": []}
    p_value = poisson_binomial_upper_tail(qs, observed_discordance)
    # Informative only when total disagreement could in principle cross the
    # threshold (product of per-task disagreement probabilities).
    min_p = 1.0
    for q in qs:
        min_p *= q
    return {
        "informative": n >= 3 and min_p < p_threshold,
        "p_value": p_value,
        "reject": p_value < p_threshold,
        "matches": n - observed_discordance,
        "total": n,
        "discordant": discordant,
    }


def poisson_binomial_upper_tail(qs: list[float], k: int) -> float:
    """P(sum of independent Bernoulli(q_i) >= k), exact via DP."""
    dist = [1.0]
    for q in qs:
        nxt = [0.0] * (len(dist) + 1)
        for i, p in enumerate(dist):
            nxt[i] += p * (1 - q)
            nxt[i + 1] += p * q
        dist = nxt
    return sum(dist[k:])


def cohort_key_for_task(
    *, control_variant_hash: str, spec: Any, task_hash: str
) -> str:
    """Re-export of the cohort key computation for controller use."""
    from roast_my_harness.spec.hashes import control_cohort_key

    return control_cohort_key(
        control_variant_hash, spec.model, spec.thinking, spec.pi_version,
        task_hash,
    )
