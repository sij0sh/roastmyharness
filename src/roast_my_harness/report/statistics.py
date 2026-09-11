"""Bootstrap statistics ported from DSE-tests analyze.py.

Seeds derive from the experiment id so every regeneration of a report is
byte-identical.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from typing import Any

BOOTSTRAP_N = 10_000


def deterministic_seed(experiment_id: str) -> int:
    return int.from_bytes(hashlib.sha256(experiment_id.encode()).digest()[:4], "big")


def rate_ci(
    outcomes: list[int], rng: random.Random
) -> tuple[float, float, float]:
    """Mean plus percentile bootstrap CI."""
    n = len(outcomes)
    if n == 0:
        return 0.0, 0.0, 0.0
    mean = sum(outcomes) / n
    means = sorted(
        sum(rng.choice(outcomes) for _ in range(n)) / n for _ in range(BOOTSTRAP_N)
    )
    lo = means[int(0.025 * BOOTSTRAP_N)]
    hi = means[min(int(0.975 * BOOTSTRAP_N), BOOTSTRAP_N - 1)]
    return mean, lo, hi


def fnum(value) -> float:
    try:
        return float(value) if value not in ("", None) else 0.0
    except (TypeError, ValueError):
        return 0.0


def trial_key(row: dict) -> tuple[str, int]:
    """Identity of one scored rollout: (task, replicate)."""
    try:
        replicate = int(row.get("replicate", 1))
    except (TypeError, ValueError):
        replicate = 1
    return str(row["task"]), replicate


def by_variant(rows: list[dict]) -> dict[str, dict[tuple[str, int], dict]]:
    grouped: dict[str, dict[tuple[str, int], dict]] = defaultdict(dict)
    for row in rows:
        variant = row["variant"]
        trial = trial_key(row)
        if trial in grouped[variant]:
            raise ValueError(
                f"duplicate report row for variant={variant!r}, task={trial[0]!r}, "
                f"replicate={trial[1]!r}"
            )
        grouped[variant][trial] = row
    return grouped


def resolved_rows(rows: list[dict]) -> list[dict]:
    """Return agent outcomes, excluding infrastructure-error trials."""
    return [row for row in rows if not row.get("exception_type")]


STRATUM_ORDER = ("easy", "medium", "hard", "unlabeled")


def variant_type(variant_id: str, variants: list[dict]) -> str:
    """Classify one arm for the comparison table.

    control is the bare-agent arm; extension/skill/context_file follow the
    variant's declared content (mixed content names both). Variants absent
    from the spec (stale rows) read as unknown, never as control.
    """
    if variant_id == "control":
        return "control"
    spec = next((v for v in variants if v.get("id") == variant_id), {})
    kinds = []
    if spec.get("extensions"):
        kinds.append("extension")
    if spec.get("skills"):
        kinds.append("skill")
    if spec.get("agents_md"):
        kinds.append("context_file")
    if not kinds:
        return "bare" if spec else "unknown"
    return "+".join(kinds)


def stratify(
    rows: list[dict],
    labels: dict[str, dict[str, Any]],
    *,
    control: str = "control",
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Per-difficulty resolve rates with task-bootstrapped CIs.

    labels maps task -> {"difficulty": ...} (catalog metadata; missing or
    unlabeled tasks form the "unlabeled" stratum so every scored task is
    accounted for). Each entry carries pooled pass/total, the mean of
    per-task rates with its CI, and the pp delta vs the control rate in
    the same stratum (None when control has no scored task there).
    """
    rates = task_rates(rows)
    pooled: dict[tuple[str, str], list[int]] = {}
    stratum_tasks: dict[str, set[str]] = {}
    for row in resolved_rows(rows):
        task = str(row["task"])
        variant = str(row["variant"])
        stratum = (labels.get(task) or {}).get("difficulty") or "unlabeled"
        pooled.setdefault((stratum, variant), []).append(int(row["resolved"]))
        stratum_tasks.setdefault(stratum, set()).add(task)
    order = [s for s in STRATUM_ORDER if s in stratum_tasks]
    order += sorted(s for s in stratum_tasks if s not in STRATUM_ORDER)
    entries: list[dict[str, Any]] = []
    for stratum in order:
        variant_rates: dict[str, list[float]] = {}
        for variant in {v for (s, v) in pooled if s == stratum} | {control}:
            table = rates.get(variant, {})
            variant_rates[variant] = sorted(
                table[task] for task in stratum_tasks[stratum] if task in table
            )
        control_list = variant_rates.get(control, [])
        control_mean = sum(control_list) / len(control_list) if control_list else None
        for variant in sorted(
            {variant for (stratum_name, variant) in pooled if stratum_name == stratum}
        ):
            task_rates_in = variant_rates.get(variant, [])
            outcomes = pooled[(stratum, variant)]
            mean, lo, hi = rate_ci(
                task_rates_in,
                random.Random(seed + deterministic_seed(f"{stratum}\0{variant}")),
            )
            entries.append(
                {
                    "stratum": stratum,
                    "variant": variant,
                    "tasks": len(task_rates_in),
                    "passed": sum(outcomes),
                    "total": len(outcomes),
                    "rate": mean,
                    "lo": lo,
                    "hi": hi,
                    "delta_pp_vs_control": (
                        None
                        if control_mean is None or variant == control
                        else 100 * (mean - control_mean)
                    ),
                }
            )
    return entries


def task_rates(rows: list[dict]) -> dict[str, dict[str, float]]:
    """Per-task pass rate over replicates, excluding infra-error trials.

    Do not treat repetitions of one task as independent benchmark tasks:
    each task contributes one rate, and the benchmark score is the mean
    of those rates.
    """
    totals: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for row in resolved_rows(rows):
        totals[str(row["variant"])][str(row["task"])].append(int(row["resolved"]))
    return {
        variant: {task: sum(outcomes) / len(outcomes) for task, outcomes in tasks.items()}
        for variant, tasks in totals.items()
    }


def paired_flips(
    rows: list[dict],
) -> list[tuple[str, str, int, int, int, list[tuple[str, str]]]]:
    """(a, b, both_pass, a_fail_b_pass, b_fail_a_pass, discordant) per pair."""
    grouped = by_variant(resolved_rows(rows))
    variants = sorted(grouped)
    flips = []
    for i, a in enumerate(variants):
        for b in variants[i + 1 :]:
            shared = sorted(set(grouped[a]) & set(grouped[b]))
            both = a_fail_b = b_fail_a = 0
            discordant: list[tuple[str, str]] = []
            for trial in shared:
                task, replicate = trial
                ra = int(grouped[a][trial]["resolved"])
                rb = int(grouped[b][trial]["resolved"])
                label = task if replicate == 1 else f"{task} (rep {replicate})"
                if ra and rb:
                    both += 1
                elif not ra and rb:
                    a_fail_b += 1
                    discordant.append((label, f"{a} fail -> {b} pass"))
                elif ra and not rb:
                    b_fail_a += 1
                    discordant.append((label, f"{a} pass -> {b} fail"))
            if shared:
                flips.append((a, b, both, a_fail_b, b_fail_a, discordant))
    return flips
