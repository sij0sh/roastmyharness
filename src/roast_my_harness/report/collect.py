"""Collect trial rows from a run directory's pier jobs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roast_my_harness.telemetry.result import is_trial_dir, trial_row


def scan_variant(
    variant_dir: Path, *, parse_results: bool = False
) -> tuple[set[str], list[tuple[Path, str, int, dict[str, Any]]]]:
    """One directory enumeration of a variant tree serving all key lookups.

    A single rglob walk classifies every entry once: pending trial-dir names
    (dirs without result.json, for the snapshot running/pending matrix) and,
    when parse_results is set, parsed result.json trial entries in sorted
    order (for newest-per-task selection). Per-key full-tree re-walks inside
    a pass would multiply this walk by the key count; callers must scan once
    per variant per pass and serve every lookup from the returned sets.
    """
    pending: set[str] = set()
    results: list[tuple[Path, str, int, dict[str, Any]]] = []
    if not variant_dir.is_dir():
        return pending, results
    for path in sorted(variant_dir.rglob("*")):
        if path.is_dir():
            if not (path / "result.json").exists():
                pending.add(path.name)
        elif parse_results and path.name == "result.json" and is_trial_dir(path.parent):
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            try:
                stamp = path.stat().st_mtime_ns
            except OSError:
                stamp = 0
            results.append((path, str(data.get("task_name") or path.parent.name), stamp, data))
    return pending, results


def pending_statuses(pending_names: set[str], tasks: list[str]) -> dict[str, str]:
    """Map each task to "~" (pending trial dir) or "." from one pending set.

    Matches the old per-task `rglob(f"{task_id}__*")` probe exactly: a task
    is pending when a pending dir name equals it or starts with task + "__".
    A character trie over the task ids keeps the match linear in the total
    name length instead of tasks x dirs.
    """
    root: dict[str, Any] = {}
    for task in tasks:
        node = root
        for ch in task:
            node = node.setdefault(ch, {})
        node[""] = task
    found: set[str] = set()
    for name in pending_names:
        node = root
        for idx, ch in enumerate(name):
            node = node.get(ch)
            if node is None:
                break
            if "" in node:
                rest = name[idx + 1 :]
                if rest == "" or rest.startswith("__"):
                    found.add(node[""])
    return {task: ("~" if task in found else ".") for task in tasks}


def newest_result_paths(variant_dir: Path, tasks: list[str]) -> dict[str, Path]:
    """Newest result.json per task from a single variant enumeration.

    One walk plus one parse per result.json serves all C lookups: Theta(F + C)
    instead of Theta(C x F). Selection mirrors latest_result_path exactly:
    newest mtime wins, ties keep the first path in sorted order.
    """
    wanted = set(tasks)
    best: dict[str, tuple[int, Path]] = {}
    _, entries = scan_variant(variant_dir, parse_results=True)
    for path, task, stamp, _data in entries:
        if task not in wanted:
            continue
        prev = best.get(task)
        if prev is None or stamp > prev[0]:
            best[task] = (stamp, path)
    return {task: path for task, (_, path) in best.items()}


def latest_result_path(jobs_root: Path, variant: str, task: str) -> Path | None:
    """Newest trial result.json under jobs_root/<variant> for one task.

    Mirrors the collect_rows selection (trial-dir check, task_name from
    result.json with the trial dir name as fallback, newest mtime wins)
    without parsing any trial's agent logs. Newest mtime wins; ties keep the
    first path in sorted order.
    """
    return newest_result_paths(jobs_root / variant, [task]).get(task)


def collect_rows(jobs_root: Path) -> list[dict[str, Any]]:
    """One row per trial result.json under <variant>/... in jobs_root.

    jobs_root is `<run>/jobs` for a roastmyharness run.
    """
    selected: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    jobs = jobs_root
    if not jobs.is_dir():
        return []
    for variant_dir in sorted(jobs.iterdir()):
        if not variant_dir.is_dir():
            continue
        _, entries = scan_variant(variant_dir, parse_results=True)
        for result_path, _task, stamp, _data in entries:
            row = trial_row(result_path, variant_dir.name)
            if not row:
                continue
            task_id = str(row.get("task") or result_path.parent.name)
            key = (variant_dir.name, task_id)
            if key not in selected or stamp >= selected[key][0]:
                selected[key] = (stamp, row)
    return [
        row
        for _, row in sorted(
            selected.values(), key=lambda item: (item[1]["variant"], item[1]["task"])
        )
    ]


def aggregate_by_variant(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Per-variant totals over collected trial rows (completed trials only).

    Keys per variant: n, resolved, input_tokens, output_tokens, wall_sec,
    cost_usd. Missing or unparsable fields contribute zero, so aggregates
    stay available mid-run.
    """
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        variant = str(row.get("variant", ""))
        agg = out.setdefault(
            variant,
            {
                "n": 0,
                "resolved": 0,
                "input_tokens": 0.0,
                "output_tokens": 0.0,
                "wall_sec": 0.0,
                "cost_usd": 0.0,
            },
        )
        agg["n"] += 1
        try:
            agg["resolved"] += int(row.get("resolved") or 0)
        except (TypeError, ValueError):
            pass
        for key in ("input_tokens", "output_tokens", "wall_sec", "cost_usd"):
            try:
                agg[key] += float(row.get(key) or 0)
            except (TypeError, ValueError):
                pass
    for agg in out.values():
        agg["wall_sec"] = round(agg["wall_sec"], 1)
        agg["cost_usd"] = round(agg["cost_usd"], 4)
    return out


FOLD_CACHE_NAME = ".fold-cache.json"


def fold_cache_path(run_dir: Path) -> Path:
    return run_dir / FOLD_CACHE_NAME


def load_fold_cache(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Load the persisted per-trial fold cache; corrupt data folds from zero."""
    try:
        data = json.loads(fold_cache_path(run_dir).read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


def save_fold_cache(run_dir: Path, cache: dict[str, dict[str, Any]]) -> None:
    """Persist the fold cache best-effort; a failed save only costs refolds."""
    try:
        fold_cache_path(run_dir).write_text(json.dumps(cache))
    except OSError:
        pass


def collect_rows_incremental(
    jobs_root: Path,
    known: dict[str, Any],
) -> tuple[list[dict], dict[str, Any], int, int]:
    """Incremental collect: fold only event bytes not seen before.

    known maps result path -> per-trial cache entry (see trial_row_cached).
    Mutated in place. Returns (rows, known, folded_count, reused_count)
    where folded counts trials with >0 event lines folded this pass and
    reused counts trials served with zero folds. Newest-wins per
    (variant, task) matches collect_rows. Legacy tuple entries from the
    earlier result-mtime cache refold once, then migrate to the new shape.
    """
    from roast_my_harness.telemetry.result import trial_row_cached

    folded_count = 0
    reused = 0
    if not jobs_root.is_dir():
        known.clear()
        return [], known, 0, 0
    seen: set[str] = set()
    per_path: dict[str, dict[str, Any]] = {}
    selected: dict[tuple[str, str], tuple[int, dict]] = {}
    for variant_dir in sorted(jobs_root.iterdir()):
        if not variant_dir.is_dir():
            continue
        _, entries = scan_variant(variant_dir, parse_results=True)
        for result_path, _task, _stamp, _data in entries:
            key = str(result_path)
            seen.add(key)
            cached = known.get(key)
            if not isinstance(cached, dict):
                cached = None
            row, updated, folded = trial_row_cached(result_path, variant_dir.name, cached)
            per_path[key] = updated
            if folded > 0:
                folded_count += 1
            else:
                reused += 1
            if row is None:
                continue
            task_id = str(row.get("task") or result_path.parent.name)
            vtask = (variant_dir.name, task_id)
            stamp = updated["result_stamp"]
            if vtask not in selected or stamp >= selected[vtask][0]:
                selected[vtask] = (stamp, row)
    for stale in [k for k in known if k not in seen]:
        del known[stale]
    known.clear()
    known.update(per_path)
    rows = [
        row
        for _, row in sorted(
            selected.values(), key=lambda item: (item[1]["variant"], item[1]["task"])
        )
    ]
    return rows, known, folded_count, reused
