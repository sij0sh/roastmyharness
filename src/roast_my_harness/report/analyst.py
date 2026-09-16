"""Post-run analyst: a deterministic reading of summary.json.

The analyst never calls a model and never fails a run. It restates what
the frozen evidence says (arm scores, highest observed rate, per-stratum
deltas with CI-overlap separation, the pre-run hypothesis for the human
reader) and writes analysis.json + analysis.md next to the other
reports. analysis.md is the terminal-facing executive report; report.md
remains the exhaustive artifact. Anything the analyst cannot compute
degrades to status "unavailable" with a reason; the controller
additionally guards the call so analyst failure is fail-open.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roast_my_harness.files import atomic_write_text
from roast_my_harness.report.metrics import outcome_label
from roast_my_harness.report.statistics import (
    resolved_rows,
    task_rates,
    variant_type,
)
from roast_my_harness.telemetry.result import fnum_or_none


def _arm_scores(rows: list[dict]) -> dict[str, dict[str, float]]:
    """resolved/total plus the mean of per-task rates, per arm."""
    totals: dict[str, dict[str, float]] = {}
    rates = task_rates(rows)
    partials: dict[str, dict[str, list[float]]] = {}
    near: dict[str, float] = {}
    for row in resolved_rows(rows):
        variant = str(row["variant"])
        arm = totals.setdefault(variant, {"resolved": 0.0, "total": 0.0})
        arm["resolved"] += int(row["resolved"])
        arm["total"] += 1
        if outcome_label(row) == "near-miss":
            near[variant] = near.get(variant, 0.0) + 1.0
        value = fnum_or_none(row.get("partial"))
        if value is not None:
            task_partials = partials.setdefault(variant, {})
            task_partials.setdefault(str(row["task"]), []).append(value)
    for variant, arm in totals.items():
        per_task = list(rates.get(variant, {}).values())
        arm["score"] = sum(per_task) / len(per_task) if per_task else 0.0
        task_means = [sum(v) / len(v) for v in partials.get(variant, {}).values()]
        arm["mean_partial"] = sum(task_means) / len(task_means) if task_means else 0.0
        arm["near_miss"] = near.get(variant, 0.0)
    return totals


def _separated(entry: dict, control_entry: dict | None) -> bool | None:
    """True when the arm's rate CI does not overlap the control CI."""
    if control_entry is None:
        return None
    try:
        lo, hi = float(entry["lo"]), float(entry["hi"])
        clo, chi = float(control_entry["lo"]), float(control_entry["hi"])
    except (KeyError, TypeError, ValueError):
        return None
    return hi < clo or chi < lo


def _series(summary: dict[str, Any]) -> dict[str, Any]:
    """Derived analysis series, accepting pre-removal summary.json files."""
    series = summary.get("analysis")
    if not isinstance(series, dict) or not series:
        legacy = summary.get("charts")
        series = legacy if isinstance(legacy, dict) else {}
    return series


def analyze_run(run_dir: Path) -> dict[str, Any]:
    """Deterministic findings for a finished run. Never raises."""
    try:
        return _analyze(run_dir)
    except Exception as error:
        return {
            "status": "unavailable",
            "reason": f"{type(error).__name__}: {error}",
        }


def _analyze(run_dir: Path) -> dict[str, Any]:
    summary_path = Path(run_dir) / "summary.json"
    summary = json.loads(summary_path.read_text())
    rows = summary.get("trials", [])
    provenance = summary.get("provenance", {})
    spec = provenance.get("spec", {})
    spec_variants = spec.get("variants", [])
    hypothesis = spec.get("hypothesis", "") or ""
    arms = _arm_scores(rows)
    types = {
        variant: variant_type(variant, spec_variants) for variant in arms
    }
    series = _series(summary)
    rate_by_variant = {
        str(e.get("variant")): e
        for e in series.get("resolve_rates", [])
        if isinstance(e, dict) and e.get("variant") is not None
    }
    control = arms.get("control", {})
    control_score = control.get("score", 0.0)
    contenders = {
        variant: arm for variant, arm in arms.items() if variant != "control"
    }
    best_name = max(contenders, key=lambda v: contenders[v]["score"], default=None)
    best = None
    if best_name is not None:
        arm = contenders[best_name]
        best = {
            "variant": best_name,
            "type": types.get(best_name, "unknown"),
            "score": arm["score"],
            "delta_pp_vs_control": 100 * (arm["score"] - control_score),
            "separated_from_control": _separated(
                rate_by_variant.get(best_name, {}),
                rate_by_variant.get("control"),
            ),
        }
    by_stratum: dict[str, dict[str, dict]] = {}
    for entry in summary.get("stratified", []):
        by_stratum.setdefault(str(entry["stratum"]), {})[str(entry["variant"])] = entry
    strata = []
    for stratum, entries in by_stratum.items():
        control_entry = entries.get("control")
        leaders = sorted(
            (v for v in entries if v != "control"),
            key=lambda v: entries[v].get("rate", 0.0),
            reverse=True,
        )
        strata.append(
            {
                "stratum": stratum,
                "leader": leaders[0] if leaders else None,
                "arms": {
                    variant: {
                        "passed": entry.get("passed"),
                        "total": entry.get("total"),
                        "rate": entry.get("rate"),
                        "delta_pp_vs_control": entry.get("delta_pp_vs_control"),
                        "separated_from_control": _separated(entry, control_entry),
                    }
                    for variant, entry in sorted(entries.items())
                },
            }
        )
    cost = series.get("cost", [])
    flips = series.get("flips", [])
    tokens = series.get("tokens", [])
    tools = series.get("tools", [])
    partial_deltas = series.get("partial_deltas", [])
    return {
        "status": "ok",
        "experiment_id": provenance.get("experiment_id"),
        "hypothesis": hypothesis,
        "hypothesis_present": bool(hypothesis.strip()),
        "arms": {
            variant: {
                "type": types.get(variant, "unknown"),
                "resolved": int(arm["resolved"]),
                "total": int(arm["total"]),
                "score": arm["score"],
                "mean_partial": arm["mean_partial"],
                "near_miss": int(arm["near_miss"]),
            }
            for variant, arm in sorted(arms.items())
        },
        "best_arm": best,
        "rates": {
            variant: {
                "rate": entry.get("rate"),
                "lo": entry.get("lo"),
                "hi": entry.get("hi"),
                "tasks": entry.get("tasks"),
            }
            for variant, entry in sorted(rate_by_variant.items())
        },
        "flips": flips,
        "cost": cost,
        "tokens": tokens,
        "tools": tools,
        "partial_deltas": partial_deltas[:10] if isinstance(partial_deltas, list) else [],
        "strata": strata,
    }


SMALL_N_THRESHOLD = 5
"""Below this per-arm task count, bootstrap CIs are reported with a caution."""


def _small_n(payload: dict[str, Any]) -> int | None:
    """Smallest arm task count, or None when arms carry no totals."""
    arms = payload.get("arms", {})
    totals = [a.get("total") for a in arms.values() if isinstance(a, dict)]
    totals = [t for t in totals if isinstance(t, (int, float))]
    if not totals:
        return None
    return int(min(totals))


def _paired_totals(payload: dict[str, Any]) -> tuple[int, int]:
    rescued = broken = 0
    for entry in payload.get("flips", []):
        if not isinstance(entry, dict):
            continue
        try:
            rescued += int(entry.get("a_fail_b_pass", 0) or 0)
            broken += int(entry.get("b_fail_a_pass", 0) or 0)
        except (TypeError, ValueError):
            continue
    return rescued, broken


def _sep_sentence(payload: dict[str, Any], separated: bool | None) -> str:
    """CI-overlap sentence with a small-n guard against overstating."""
    small = _small_n(payload)
    if separated is True:
        if small is not None and small < SMALL_N_THRESHOLD:
            rescued, broken = _paired_totals(payload)
            return (
                "Observed task-bootstrap intervals do not overlap, but "
                f"n={small} per arm is insufficient for a meaningful "
                "uncertainty estimate. The paired result for this run is "
                f"{rescued} rescued and {broken} broken."
            )
        return "The 95% intervals do not overlap, so the separation is visible at this scale."
    if separated is False:
        return (
            "The confidence intervals overlap (or no control baseline exists), "
            "so this run is evidence worth confirming rather than a strong separation."
        )
    return "No control baseline exists for a separation check."


def _fmt_tokens(value: Any) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.2f}m"
    if num >= 1000:
        return f"{num / 1000:.1f}k"
    return f"{num:.0f}"


def _fmt_signed(value: Any, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):+.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _bar(frac: float, width: int = 20) -> str:
    """Restrained ASCII bar for 0..1 fractions; used only for resolve/flips."""
    try:
        frac = float(frac)
    except (TypeError, ValueError):
        frac = 0.0
    frac = min(max(frac, 0.0), 1.0)
    filled = int(round(frac * width))
    return "#" * filled + " " * (width - filled)


def _fmt_pct(value: Any) -> str:
    try:
        return f"{100 * float(value):.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_pp(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):+.1f}pp"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_pct_delta(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):+.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def artifact_links(run_dir: Path | str | None) -> dict[str, str]:
    """Absolute file:// links for the four terminal artifacts."""
    if run_dir is None:
        return {}
    rd = Path(run_dir)
    return {
        name: Path(rd / name).as_uri()
        for name in ("report.md", "summary.csv", "summary.json", "analysis.json")
    }


def render_markdown(payload: dict[str, Any], run_dir: Path | str | None = None) -> str:
    """Human-readable analysis.md. Pure rendering, no model judgment."""
    if payload.get("status") != "ok":
        return (
            "# Analysis\n\n"
            f"Analysis unavailable: {payload.get('reason', 'unknown')}.\n"
            "The run reports above stand on their own.\n"
        )
    lines = ["# Analysis", ""]
    lines.append("Deterministic summary of summary.json; no model judgment.")
    lines.append("")
    links = artifact_links(run_dir)
    lines.append("## Artifacts")
    lines.append("")
    if links:
        for name in ("report.md", "summary.csv", "summary.json", "analysis.json"):
            lines.append(f"- [{name}]({links[name]})")
    else:
        for name in ("report.md", "summary.csv", "summary.json", "analysis.json"):
            lines.append(f"- {name}")
    lines.append("")
    hypothesis = payload.get("hypothesis") or ""
    if hypothesis.strip():
        lines.append("## Frozen hypothesis")
        lines.append("")
        lines.append(f"> {hypothesis.strip()}")
        lines.append("")
        lines.append("Compare the deltas below against it by hand; the")
        lines.append("analyst does not parse prose.")
        lines.append("")
    else:
        lines.append("No hypothesis was frozen at authoring time.")
        lines.append("")
    arms = payload.get("arms", {})
    rates = payload.get("rates", {})
    control_score = arms.get("control", {}).get("score")

    lines.append("## Overall")
    lines.append("")
    lines.append("| arm | resolved | score | mean partial | near misses |")
    lines.append("|---|---|---|---|---|")
    for variant in sorted(arms):
        arm = arms[variant]
        lines.append(
            f"| {variant} | {arm['resolved']}/{arm['total']} | "
            f"{100 * arm['score']:.1f}% | "
            f"{100 * arm.get('mean_partial', 0.0):.1f}% | {arm.get('near_miss', 0)} |"
        )
    lines.append("")

    lines.append("## Resolve rate")
    lines.append("")
    for variant in sorted(arms):
        arm = arms[variant]
        rate = rates.get(variant, {})
        lo, hi = rate.get("lo"), rate.get("hi")
        ci = (
            f"[{_fmt_pct(lo)}, {_fmt_pct(hi)}]"
            if lo is not None and hi is not None
            else "n/a"
        )
        delta = ""
        if variant != "control" and control_score is not None:
            try:
                delta = f" {_fmt_pp(100 * (arm['score'] - control_score))}"
            except (TypeError, ValueError):
                delta = ""
        lines.append(
            f"{variant} {_fmt_pct(arm['score'])} [{_bar(arm['score'])}] "
            f"{ci}{delta}"
        )
    lines.append("")

    flips = payload.get("flips", [])
    if flips:
        lines.append("## Paired outcomes")
        lines.append("")
        for entry in flips:
            if not isinstance(entry, dict):
                continue
            a, b = entry.get("a"), entry.get("b")
            rescued = entry.get("a_fail_b_pass", 0)
            broken = entry.get("b_fail_a_pass", 0)
            both = entry.get("both_pass", 0)
            try:
                net = int(rescued) - int(broken)
            except (TypeError, ValueError):
                net = 0
            lines.append(f"### {a} vs {b}")
            lines.append("")
            lines.append(f"both passed: {both}")
            lines.append(f"rescued by {b}: {rescued} {'+' * min(int(rescued or 0), 20)}")
            lines.append(f"broken by {b}: {broken} {'-' * min(int(broken or 0), 20)}")
            lines.append(f"net paired movement: {net:+d} tasks")
            lines.append("")
    else:
        lines.append("## Paired outcomes")
        lines.append("")
        lines.append("No shared tasks to pair across arms.")
        lines.append("")

    tokens = payload.get("tokens", [])
    if tokens:
        lines.append("## Token usage — mean per valid task")
        lines.append("")
        lines.append(
            "cache read accumulates cache-read (cached-prefix) traffic across turns; "
            "cache write is the cache-write side."
        )
        lines.append("| arm | input | cache read | cache write | output | reasoning |")
        lines.append("|---|---|---|---|---|---|")
        for entry in tokens:
            if not isinstance(entry, dict):
                continue
            lines.append(
                f"| {entry.get('variant')} | {_fmt_tokens(entry.get('input_mean'))} | "
                f"{_fmt_tokens(entry.get('cache_read_mean'))} | "
                f"{_fmt_tokens(entry.get('cache_write_mean'))} | "
                f"{_fmt_tokens(entry.get('output_mean'))} | "
                f"{_fmt_tokens(entry.get('reasoning_mean'))} |"
            )
        lines.append("")
        lines.append("Difference vs control")
        lines.append("")
        lines.append("| arm | input | cache read | cache write | output | reasoning |")
        lines.append("|---|---|---|---|---|---|")
        for entry in tokens:
            if not isinstance(entry, dict) or entry.get("variant") == "control":
                continue
            lines.append(
                f"| {entry.get('variant')} | "
                f"{_fmt_pct_delta(entry.get('input_delta_pct'))} | "
                f"{_fmt_pct_delta(entry.get('cache_read_delta_pct'))} | "
                f"{_fmt_pct_delta(entry.get('cache_write_delta_pct'))} | "
                f"{_fmt_pct_delta(entry.get('output_delta_pct'))} | "
                f"{_fmt_pct_delta(entry.get('reasoning_delta_pct'))} |"
            )
        lines.append("")
        lines.append("Token totals")
        lines.append("")
        lines.append("| arm | input | cache read | cache write | output |")
        lines.append("|---|---|---|---|---|")
        for entry in tokens:
            if not isinstance(entry, dict):
                continue
            lines.append(
                f"| {entry.get('variant')} | {_fmt_tokens(entry.get('input_total'))} | "
                f"{_fmt_tokens(entry.get('cache_read_total'))} | "
                f"{_fmt_tokens(entry.get('cache_write_total'))} | "
                f"{_fmt_tokens(entry.get('output_total'))} |"
            )
        lines.append("")

    tools = payload.get("tools", [])
    if tools:
        lines.append("## Tool and read behavior — mean per valid task")
        lines.append("")
        lines.append(
            "| arm | tools | reads | rereads | overlap rereads | files | reads/file | "
            "tool failures |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for entry in tools:
            if not isinstance(entry, dict):
                continue
            lines.append(
                f"| {entry.get('variant')} | "
                f"{_fmt_signed(entry.get('mean_tool_calls'))[1:]} | "
                f"{_fmt_signed(entry.get('mean_read_calls'))[1:]} | "
                f"{_fmt_signed(entry.get('mean_rereads'))[1:]} | "
                f"{_fmt_signed(entry.get('mean_overlap_rereads'))[1:]} | "
                f"{_fmt_signed(entry.get('mean_distinct_files'))[1:]} | "
                f"{_fmt_signed(entry.get('mean_reads_per_file'))[1:]} | "
                f"{_fmt_signed(entry.get('mean_tool_failures'))[1:]} |"
            )
        lines.append("")
        lines.append("Difference vs control (calls/task)")
        lines.append("")
        for entry in tools:
            if not isinstance(entry, dict) or entry.get("variant") == "control":
                continue
            lines.append(
                f"{entry.get('variant')}: tools {_fmt_signed(entry.get('tool_calls_delta_vs_control'))} "
                f"· reads {_fmt_signed(entry.get('read_calls_delta_vs_control'))} "
                f"· rereads {_fmt_signed(entry.get('rereads_delta_vs_control'))} "
                f"· overlap rereads {_fmt_signed(entry.get('overlap_rereads_delta_vs_control'))} "
                f"· files {_fmt_signed(entry.get('distinct_files_delta_vs_control'))} "
                f"· tool failures {_fmt_signed(entry.get('tool_failures_delta_vs_control'))}"
            )
        lines.append("")

    cost = payload.get("cost", [])
    if cost:
        lines.append("## Runtime / cost — mean per valid task")
        lines.append("")
        lines.append("| arm | wall/task | LLM calls | cost/task | cost/resolve |")
        lines.append("|---|---|---|---|---|")
        llm_by_variant = {}
        for entry in tokens if isinstance(tokens, list) else []:
            if isinstance(entry, dict):
                llm_by_variant[entry.get("variant")] = entry.get("llm_calls_mean")
        for entry in cost:
            if not isinstance(entry, dict):
                continue
            variant = entry.get("variant")
            try:
                wall = float(entry.get("mean_wall_sec", 0.0))
                usd = float(entry.get("mean_cost_usd", 0.0))
            except (TypeError, ValueError):
                continue
            llm = llm_by_variant.get(variant)
            try:
                llm_disp = f"{float(llm):.1f}" if llm is not None else "n/a"
            except (TypeError, ValueError):
                llm_disp = "n/a"
            per_resolve = entry.get("cost_per_resolve")
            try:
                resolve_disp = f"${float(per_resolve):.2f}" if per_resolve is not None else "n/a"
            except (TypeError, ValueError):
                resolve_disp = "n/a"
            lines.append(
                f"| {variant} | {wall / 60:.1f}m | {llm_disp} | "
                f"${usd:.2f} | {resolve_disp} |"
            )
        lines.append("")

    deltas = payload.get("partial_deltas", [])
    if deltas:
        lines.append("## Largest partial-credit movements")
        lines.append("")
        lines.append("| task | variant | delta |")
        lines.append("|---|---|---|")
        for entry in deltas[:10]:
            if not isinstance(entry, dict):
                continue
            try:
                delta = float(entry.get("delta", 0.0))
            except (TypeError, ValueError):
                continue
            width = min(int(abs(delta) * 20), 20)
            marks = "+" * width if delta >= 0 else "-" * width
            lines.append(
                f"| {entry.get('task')} | {entry.get('variant')} | "
                f"{delta:+.2f} {marks} |"
            )
        lines.append("")

    best = payload.get("best_arm")
    if best:
        sep = best.get("separated_from_control")
        if sep is True:
            small = _small_n(payload)
            if small is not None and small < SMALL_N_THRESHOLD:
                rescued, broken = _paired_totals(payload)
                sep_text = (
                    "observed task-bootstrap intervals do not overlap, but "
                    f"n={small} per arm is insufficient for a meaningful "
                    "uncertainty estimate. The paired result for this run is "
                    f"{rescued} rescued and {broken} broken"
                )
            else:
                sep_text = "separated"
        elif sep is False:
            sep_text = "overlapping"
        else:
            sep_text = "no control baseline"
        lines.append(
            f"Highest observed resolve rate: {best['variant']} "
            f"({100 * best['score']:.1f}%)."
        )
        lines.append(
            f"Delta vs control: {_fmt_pp(best['delta_pp_vs_control'])}. "
            f"95% intervals: {sep_text}."
        )
        lines.append("")
    for stratum in payload.get("strata", []):
        lines.append(f"## Stratum {stratum['stratum']}")
        lines.append("")
        if stratum["leader"]:
            lines.append(f"Leader: {stratum['leader']}.")
        for variant, arm in stratum["arms"].items():
            if variant == "control":
                continue
            delta_text = _fmt_pp(arm["delta_pp_vs_control"])
            sep = arm["separated_from_control"]
            sep_text = (
                "separated"
                if sep is True
                else ("overlapping" if sep is False else "no control baseline")
            )
            lines.append(f"- {variant}: {delta_text} vs control ({sep_text}).")
        lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    lines.append(_interpretation(payload))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _interpretation(payload: dict[str, Any]) -> str:
    """Deterministic closing paragraph: observation first, strength second."""
    arms = payload.get("arms", {})
    best = payload.get("best_arm") or {}
    flips = payload.get("flips", [])
    net = 0
    for entry in flips:
        if not isinstance(entry, dict):
            continue
        try:
            net += int(entry.get("a_fail_b_pass", 0)) - int(entry.get("b_fail_a_pass", 0))
        except (TypeError, ValueError):
            continue
    parts = []
    if best.get("variant") is not None:
        parts.append(
            f"{best['variant']} has the highest observed resolve rate "
            f"({100 * best.get('score', 0.0):.1f}%, "
            f"{_fmt_pp(best.get('delta_pp_vs_control'))} vs control)."
        )
    else:
        parts.append("No non-control arm was observed.")
    if flips:
        parts.append(f"Net paired movement across shared tasks is {net:+d}.")
    leaders = [
        f"{s['stratum']}: {s['leader']}"
        for s in payload.get("strata", [])
        if isinstance(s, dict) and s.get("leader")
    ]
    if leaders:
        parts.append("Stratum leaders: " + ", ".join(leaders) + ".")
    separated = best.get("separated_from_control") if best else None
    parts.append(_sep_sentence(payload, separated))
    if len(arms) <= 2 and all(a.get("total", 0) and a["total"] < 30 for a in arms.values()):
        parts.append("At small task counts, paired flips carry more weight than headline rates.")
    return " ".join(parts)


def write_analysis(run_dir: Path) -> Path:
    """Write analysis.json + analysis.md. Returns the JSON path."""
    payload = analyze_run(Path(run_dir))
    out = Path(run_dir) / "analysis.json"
    atomic_write_text(out, json.dumps(payload, indent=2) + "\n")
    atomic_write_text(
        Path(run_dir) / "analysis.md", render_markdown(payload, Path(run_dir))
    )
    return out
