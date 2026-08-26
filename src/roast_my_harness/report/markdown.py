"""report.md generation: ported sections plus provenance and disclosure."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from roast_my_harness.report.collect import collect_rows
from roast_my_harness.report.statistics import (
    by_variant,
    deterministic_seed,
    fnum,
    paired_flips,
    rate_ci,
)


def generate_report(
    run_dir: Path,
    *,
    experiment_id: str,
    provenance: dict[str, Any],
    rows: list[dict[str, Any]] | None = None,
) -> Path:
    rows = rows if rows is not None else collect_rows(run_dir)
    rng = random.Random(deterministic_seed(experiment_id))
    grouped = by_variant(rows)
    variants = sorted(grouped)

    lines: list[str] = [f"# RoastMyHarness report: {experiment_id}\n"]

    # 1. Configuration and provenance.
    lines.append("## Configuration and provenance\n")
    lines.append("```json")
    import json

    lines.append(json.dumps(provenance, indent=2, default=str))
    lines.append("```\n")

    # 2. Completion and error summary.
    lines.append("## Completion summary\n")
    lines.append("| variant | trials | pass | fail | error |")
    lines.append("|---|---|---|---|---|")
    for v in variants:
        tasks = list(grouped[v].values())
        n = len(tasks)
        p = sum(1 for t in tasks if int(t["resolved"]) == 1)
        e = sum(1 for t in tasks if t.get("exception_type"))
        lines.append(f"| {v} | {n} | {p} | {n - p - e} | {e} |")
    lines.append("")

    # 3. Resolve rates with bootstrap CIs.
    lines.append("## Resolve rates\n")
    lines.append(
        "| variant | resolved | rate | 95% CI | mean tokens in | mean tokens out "
        "| mean cached in | mean cost | mean wall sec |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for v in variants:
        tasks = list(grouped[v].values())
        outcomes = [int(t["resolved"]) for t in tasks]
        mean, lo, hi = rate_ci(outcomes, rng)
        n = len(tasks) or 1
        toks_in = sum(fnum(t["input_tokens"]) for t in tasks) / n
        toks_out = sum(fnum(t["output_tokens"]) for t in tasks) / n
        toks_cached = sum(fnum(t.get("cache_tokens", "")) for t in tasks) / n
        cost = sum(fnum(t["cost_usd"]) for t in tasks) / n
        wall = sum(fnum(t["wall_sec"]) for t in tasks) / n
        lines.append(
            f"| {v} | {sum(outcomes)}/{len(tasks)} | {100 * mean:.1f}% | "
            f"[{100 * lo:.1f}, {100 * hi:.1f}] | {toks_in / 1000:.0f}k | "
            f"{toks_out / 1000:.0f}k | {toks_cached / 1000:.0f}k | ${cost:.2f} | "
            f"{wall / 60:.0f}m |"
        )
    lines.append("")

    # 4-5. Paired flips and discordant tasks.
    flips = paired_flips(rows)
    if flips:
        lines.append("## Paired flips\n")
        for a, b, both, a_fail_b, b_fail_a, discordant in flips:
            lines.append(
                f"### {a} vs {b}\n\n"
                f"| both pass | {a} fail, {b} pass (rescued) | "
                f"{a} pass, {b} fail (broken) |\n|---|---|---|\n"
                f"| {both} | {a_fail_b} | {b_fail_a} |\n"
            )
            for task, note in discordant:
                lines.append(f"- {task}: {note}")
            if discordant:
                lines.append("")

    # 6. Token, cache, cost, wall time.
    lines.append("## Cost and timing\n")
    lines.append("| variant | mean cost | mean wall | sum input | sum output |")
    lines.append("|---|---|---|---|---|")
    for v in variants:
        tasks = list(grouped[v].values())
        n = len(tasks) or 1
        lines.append(
            f"| {v} | ${sum(fnum(t['cost_usd']) for t in tasks) / n:.2f} | "
            f"{sum(fnum(t['wall_sec']) for t in tasks) / n / 60:.0f}m | "
            f"{sum(fnum(t['input_tokens']) for t in tasks) / 1000:.0f}k | "
            f"{sum(fnum(t['output_tokens']) for t in tasks) / 1000:.0f}k |"
        )
    lines.append("")

    # 7. Context and compaction.
    lines.append("## Compaction behavior\n")
    lines.append(
        "| variant | trials with compaction | total compactions | "
        "mean peak context tokens |"
    )
    lines.append("|---|---|---|---|")
    peak_key = "peak_input_cache_tokens"
    if not any(
        t.get(peak_key) not in ("", None, "0")
        for tasks in grouped.values()
        for t in tasks.values()
    ):
        peak_key = "peak_context_tokens"
    for v in variants:
        tasks = list(grouped[v].values())
        with_comp = [t for t in tasks if fnum(t.get("summarization_count", "")) > 0]
        total = sum(int(fnum(t.get("summarization_count", ""))) for t in tasks)
        peak = sum(fnum(t.get(peak_key, "")) for t in tasks) / max(len(tasks), 1)
        lines.append(
            f"| {v} | {len(with_comp)}/{len(tasks)} | {total} | {peak / 1000:.0f}k |"
        )
    lines.append("")

    # 8. Tool and read behavior.
    tool_keys = (
        "tool_calls", "read_calls", "read_rereads",
        "read_overlap_rereads", "distinct_read_files",
    )
    if any(
        t.get(k) not in ("", None)
        for tasks in grouped.values()
        for t in tasks.values()
        for k in tool_keys
    ):
        lines.append("## Tool and read behavior\n")
        lines.append(
            "| variant | mean tool calls | mean read calls | mean rereads | "
            "mean overlap rereads | mean distinct files | mean reads/file |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for v in variants:
            tasks = list(grouped[v].values())
            n = max(len(tasks), 1)
            means = {k: sum(fnum(t.get(k, "")) for t in tasks) / n for k in tool_keys}
            per_file = (
                means["read_calls"] / means["distinct_read_files"]
                if means["distinct_read_files"]
                else 0.0
            )
            lines.append(
                f"| {v} | {means['tool_calls']:.0f} | {means['read_calls']:.0f} | "
                f"{means['read_rereads']:.0f} | "
                f"{means['read_overlap_rereads']:.0f} | "
                f"{means['distinct_read_files']:.0f} | {per_file:.1f} |"
            )
        lines.append("")

    # 9. Historical control disclosure.    
    lines.append("## Historical control disclosure\n")
    reuse = provenance.get("control_reuse") or {}
    reused = provenance.get("reused_control_observations", 0)
    if reuse.get("enabled") and reuse.get("accepted") and reused:
        lines.append(
            f"- {reused} historic control observations were reused across "
            f"{len(reuse.get('reused_tasks', []))} tasks."
        )
        counts = reuse.get("reused_counts", {})
        ranges = reuse.get("reused_date_ranges", {})
        for task in sorted(counts):
            lo, hi = ranges.get(task, ["", ""])
            span = f" ({lo[:10]}..{hi[:10]})" if lo else ""
            lines.append(f"  - {task}: {counts[task]} observations{span}")
        lines.append(
            "- Reused controls are NOT contemporaneous paired observations; "
            "paired-flip tables cover only run-matched pairs."
        )
        fresh = reuse.get("fresh_control_tasks", [])
        if fresh:
            lines.append(
                f"- Control tasks run fresh (insufficient or ineligible "
                f"history): {', '.join(fresh)}."
            )
        sentinel = reuse.get("sentinel")
        if sentinel:
            verdict = "REJECTED (drift suspected)" if sentinel.get("reject") \
                else "passed"
            if not sentinel.get("informative"):
                verdict += " but sample too small to be informative"
            lines.append(
                f"- Sentinel check: {sentinel.get('matches')}/"
                f"{sentinel.get('total')} agreed, p={sentinel.get('p_value')}. "
                f"Result: {verdict}."
            )
    elif reuse.get("enabled"):
        lines.append(
            "- No historic control observations were reused for this run "
            f"(policy={reuse.get('policy')}, accepted={reuse.get('accepted')})."
        )
    else:
        lines.append("- No historic control observations were reused for this run.")

    # 10. Interpretation guide.
    lines.append("\n## Interpretation guide\n")
    lines.append(
        "- At small task counts, resolve-rate differences alone are not "
        "signal; look at paired flips first.\n"
        "- A large rescued-vs-broken imbalance in one direction is the "
        "strongest evidence available at this scale.\n"
        "- Cost columns may read 0 when the gateway does not report per-call "
        "costs; use token columns then.\n"
        "- cache_tokens is the sum of per-call cache-read tokens across turns "
        "(total cached-prefix traffic), not a session-unique count.\n"
        "- summarization_count counts pi-native compaction events; "
        "extension-internal rewrites (folding, projection, rtk rewrites) do "
        "not appear in it.\n"
        "- Bootstrap intervals use a seed derived from the experiment id; "
        "regenerating this report reproduces it byte for byte."
    )

    out = run_dir / "report.md"
    out.write_text("\n".join(lines) + "\n")
    return out
