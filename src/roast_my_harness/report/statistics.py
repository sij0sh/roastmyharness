"""Bootstrap statistics ported from DSE-tests analyze.py.

Seeds derive from the experiment id so every regeneration of a report is
byte-identical.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict

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


def by_variant(rows: list[dict]) -> dict[str, dict[str, dict]]:
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        grouped[row["variant"]][row["task"]] = row
    return grouped


def paired_flips(
    rows: list[dict],
) -> list[tuple[str, str, int, int, int, list[tuple[str, str]]]]:
    """(a, b, both_pass, a_fail_b_pass, b_fail_a_pass, discordant) per pair."""
    grouped = by_variant(rows)
    variants = sorted(grouped)
    flips = []
    for i, a in enumerate(variants):
        for b in variants[i + 1 :]:
            shared = sorted(set(grouped[a]) & set(grouped[b]))
            both = a_fail_b = b_fail_a = 0
            discordant: list[tuple[str, str]] = []
            for task in shared:
                ra = int(grouped[a][task]["resolved"])
                rb = int(grouped[b][task]["resolved"])
                if ra and rb:
                    both += 1
                elif not ra and rb:
                    a_fail_b += 1
                    discordant.append((task, f"{a} fail -> {b} pass"))
                elif ra and not rb:
                    b_fail_a += 1
                    discordant.append((task, f"{a} pass -> {b} fail"))
            if shared:
                flips.append((a, b, both, a_fail_b, b_fail_a, discordant))
    return flips
