"""report.md generation: ported sections plus provenance and disclosure."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from roast_my_harness.files import atomic_write_text
from roast_my_harness.report.collect import collect_rows
from roast_my_harness.report.dimensions import dimension_summary, has_dimensions
from roast_my_harness.report.statistics import (
    by_variant,
    deterministic_seed,
    fnum,
    paired_flips,
    rate_ci,
    resolved_rows,
    stratify,
    task_rates,
    variant_type,
)
from roast_my_harness.tasks.catalog import load_catalog
from roast_my_harness.telemetry.result import DETERMINISTIC_KEY, JUDGE_KEY


def task_labels(provenance: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Catalog difficulty/duration labels keyed by task id.

    Labels come from the benchmark catalog recorded in provenance, never
    from task directories. Unknown tasks simply have no entry.
    """
    tasks_path = provenance.get("tasks_path")
    if not tasks_path:
        return {}
    catalog = load_catalog(Path(tasks_path))
    if catalog is None:
        return {}
    return {
        task_id: {"difficulty": meta.difficulty, "duration": meta.duration}
        for task_id, meta in catalog.tasks.items()
    }


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
    rates = task_rates(rows)

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
    spec_variants = (provenance.get("spec") or {}).get("variants", [])
    lines.append(
        "| variant | type | resolved | rate | 95% CI | mean tokens in | mean tokens out "
        "| mean cached in | mean cost | mean wall sec |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for v in variants:
        tasks = list(grouped[v].values())
        valid_tasks = resolved_rows(tasks)
        outcomes = [int(t["resolved"]) for t in valid_tasks]
        # Score each task once: the mean of its per-task pass rates, with
        # the bootstrap over tasks (repetitions of one task are not
        # independent benchmark tasks). Single-repetition runs reduce to
        # the old row bootstrap exactly.
        per_task = sorted(rates.get(v, {}).values())
        mean, lo, hi = rate_ci(per_task, rng)
        n = len(valid_tasks) or 1
        toks_in = sum(fnum(t["input_tokens"]) for t in valid_tasks) / n
        toks_out = sum(fnum(t["output_tokens"]) for t in valid_tasks) / n
        toks_cached = sum(fnum(t.get("cache_tokens", "")) for t in valid_tasks) / n
        cost = sum(fnum(t["cost_usd"]) for t in valid_tasks) / n
        wall = sum(fnum(t["wall_sec"]) for t in valid_tasks) / n
        lines.append(
            f"| {v} | {variant_type(v, spec_variants)} | {sum(outcomes)}/{len(outcomes)} | "
            f"{100 * mean:.1f}% | "
            f"[{100 * lo:.1f}, {100 * hi:.1f}] | {toks_in / 1000:.0f}k | "
            f"{toks_out / 1000:.0f}k | {toks_cached / 1000:.0f}k | ${cost:.2f} | "
            f"{wall / 60:.0f}m |"
        )
    lines.append("")
    reuse = provenance.get("control_reuse") or {}
    if reuse.get("enabled") and reuse.get("total_reused"):
        lines.append(
            "Control resolve rates use fresh current-run trials only. "
            "Historic control observations are disclosed separately below."
        )
        lines.append("")

    if has_dimensions(rows):
        lines.append("## Scores by dimension\n")
        lines.append(
            "Deterministic and judge scores are mean task scores (0..1) with "
            "task-bootstrapped CIs, reported separately so a blended number "
            "never reads as purely objective. The combined outcome stays the "
            "resolve rate above; each eval's contract defines how its "
            "verifier folds dimensions into the scalar reward."
        )
        lines.append(
            "| variant | deterministic | 95% CI | judge | 95% CI | judge model |"
        )
        lines.append("|---|---|---|---|---|---|")
        dims = dimension_summary(
            rows, seed=deterministic_seed(f"{experiment_id}\0dimensions")
        )
        for v in variants:
            entry = dims.get(v, {})
            det = entry.get(DETERMINISTIC_KEY)
            judge = entry.get(JUDGE_KEY)
            models = ", ".join(entry.get("judge_models", [])) or "—"
            det_disp = (
                f"{100 * det['mean']:.1f}% ({det['tasks']})" if det else "—"
            )
            det_ci = (
                f"[{100 * det['lo']:.1f}, {100 * det['hi']:.1f}]" if det else "—"
            )
            judge_disp = (
                f"{100 * judge['mean']:.1f}% ({judge['tasks']})" if judge else "—"
            )
            judge_ci = (
                f"[{100 * judge['lo']:.1f}, {100 * judge['hi']:.1f}]"
                if judge
                else "—"
            )
            lines.append(
                f"| {v} | {det_disp} | {det_ci} | {judge_disp} | "
                f"{judge_ci} | {models} |"
            )
        lines.append("")

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

    # 4. Difficulty stratification.
    labels = task_labels(provenance)
    strata = stratify(
        rows, labels, seed=deterministic_seed(f"{experiment_id}\0strata")
    )
    if strata:
        lines.append("## Results by task difficulty\n")
        lines.append(
            "Difficulty bands the Luna High published solve rate "
            "(easy >= 3/4, medium = 2/4, hard <= 1/4); see the benchmark "
            "catalog for per-task basis. Rates are means of per-task rates "
            "with task-bootstrapped CIs, deltas vs control in the stratum."
        )
        lines.append(
            "| difficulty | variant | tasks | resolved | rate | 95% CI | delta vs control |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for entry in strata:
            delta = entry["delta_pp_vs_control"]
            delta_disp = "—" if delta is None else f"{delta:+.1f}pp"
            lines.append(
                f"| {entry['stratum']} | {entry['variant']} | {entry['tasks']} | "
                f"{entry['passed']}/{entry['total']} | {100 * entry['rate']:.1f}% | "
                f"[{100 * entry['lo']:.1f}, {100 * entry['hi']:.1f}] | {delta_disp} |"
            )
        lines.append("")
        duration_known = sum(
            1 for task, meta in labels.items() if meta.get("duration")
        )
        lines.append(
            f"- Duration axis omitted: {duration_known} tasks carry duration "
            "labels (no wall-time calibration yet)."
        )
        lines.append("")

    # 5. Token, cache, cost, wall time.
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

    # 6. Context and compaction.
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

    # 7. Tool and read behavior.
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

    lines.append("## Historical control disclosure\n")
    reuse = provenance.get("control_reuse") or {}
    reused = provenance.get("reused_control_observations", 0)
    mode = reuse.get("mode", "fresh")
    status = reuse.get("status")
    if reuse.get("enabled") and reuse.get("accepted") and reused:
        lines.append(
            f"- Historic control ({mode}, {status}): {reused} observations "
            f"reused across {len(reuse.get('reused_tasks', []))} tasks."
        )
        counts = reuse.get("reused_counts", {})
        ranges = reuse.get("reused_date_ranges", {})
        for task in sorted(counts):
            lo, hi = ranges.get(task, ["", ""])
            span = f" ({lo[:10]}..{hi[:10]})" if lo else ""
            lines.append(f"  - {task}: {counts[task]} observations{span}")
        lines.append(
            "- Reused controls are not contemporaneous paired observations; "
            "paired-flip tables cover only run-matched pairs."
        )
        baseline = reuse.get("baseline") or {}
        ext_variants = [v for v in variants if v != "control"]
        if baseline and ext_variants:
            lines.append(
                "- Historical baseline vs fresh extension "
                "(historical rates are labeled context, not paired evidence):"
            )
            lines.append("")
            lines.append(
                "| task | historic control | variant | extension | delta |"
            )
            lines.append("|---|---|---|---|---|")
            for task in sorted(baseline):
                hist = baseline[task]
                hist_disp = (
                    f"{hist.get('pass', 0)}/{hist.get('total', 0)} = "
                    f"{100 * float(hist.get('rate', 0.0)):.1f}%"
                )
                for ext in ext_variants:
                    ext_rows = [
                        t for t in resolved_rows(list(grouped[ext].values()))
                        if str(t.get("task")) == task
                    ]
                    if not ext_rows:
                        continue
                    passed = sum(int(t["resolved"]) for t in ext_rows)
                    total = len(ext_rows)
                    rate = passed / total
                    delta_pp = 100 * (rate - float(hist.get("rate", 0.0)))
                    lines.append(
                        f"| {task} | {hist_disp} | {ext} | "
                        f"{passed}/{total} = {100 * rate:.1f}% | "
                        f"{delta_pp:+.1f}pp |"
                    )
            lines.append("")
        fresh = reuse.get("fresh_control_tasks", [])
        if fresh:
            lines.append(f"- Control tasks run fresh: {', '.join(fresh)}.")
        out_of_scope = reuse.get("out_of_scope_tasks", [])
        if out_of_scope:
            lines.append(
                "- Control tasks out of scope (no history, "
                f"intersection scope): {', '.join(out_of_scope)}."
            )
        sentinel = reuse.get("sentinel")
        if sentinel:
            verdict = "REJECTED (drift suspected)" if sentinel.get("reject") else "passed"
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
            f"(mode={mode}, status={status})."
        )
    else:
        lines.append("- No historic control observations were reused for this run.")

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
    atomic_write_text(out, "\n".join(lines) + "\n")
    return out
