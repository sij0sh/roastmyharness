"""Collect trial rows from a run directory's pier jobs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roast_my_harness.runner.reconcile import replicate_of
from roast_my_harness.telemetry.result import is_trial_dir, trial_row


def scan_variant(
    variant_dir: Path, *, parse_results: bool = False
) -> tuple[set[tuple[str, int]], list[tuple[Path, str, int, int, dict[str, Any]]]]:
    """One directory enumeration of a variant tree serving all key lookups.

    A single rglob walk classifies every entry once: pending trial-dir
    (name, replicate) pairs (dirs without result.json, for the snapshot
    running/pending matrix) and, when parse_results is set, parsed
    result.json trial entries in sorted order (for newest-per-trial
    selection). Per-key full-tree re-walks inside a pass would multiply
    this walk by the key count; callers must scan once per variant per
    pass and serve every lookup from the returned sets.
    """
    pending: set[tuple[str, int]] = set()
    results: list[tuple[Path, str, int, int, dict[str, Any]]] = []
    if not variant_dir.is_dir():
        return pending, results
    for path in sorted(variant_dir.rglob("*")):
        if path.is_dir():
            try:
                has_result = (path / "result.json").exists()
            except OSError:
                # Relocated agent homes can contain untraversable dirs
                # (e.g. claude session state owned by the container uid).
                # Uninspectable means unclassifiable: skip, never re-run.
                continue
            if not has_result:
                pending.add((path.name, replicate_of(variant_dir, path)))
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
            results.append(
                (
                    path,
                    str(data.get("task_name") or path.parent.name),
                    stamp,
                    replicate_of(variant_dir, path.parent),
                    data,
                )
            )
    return pending, results


def pending_statuses(
    pending_names: set[tuple[str, int]] | set[str], tasks: list[str]
) -> dict[str, str]:
    """Map each task to "~" (pending trial dir) or "." from one pending set.

    Matches the old per-task `rglob(f"{task_id}__*")` probe exactly: a task
    is pending when a pending dir name equals it or starts with task + "__".
    A character trie over the task ids keeps the match linear in the total
    name length instead of tasks x dirs. Entries may be bare names or
    (name, replicate) pairs; the replicate only scopes per-trial lookups
    in pending_replicates.
    """
    root: dict[str, Any] = {}
    for task in tasks:
        node = root
        for ch in task:
            node = node.setdefault(ch, {})
        node[""] = task
    found: set[str] = set()
    for entry in pending_names:
        name = entry[0] if isinstance(entry, tuple) else entry
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


def pending_replicates(
    pending: set[tuple[str, int]], tasks: list[str], repetitions: int
) -> dict[tuple[str, int], str]:
    """Map each (task, replicate) trial to "~" (pending) or ".".

    A trial is pending when a pending dir in its replicate scope matches
    the task name exactly or as a task + "__" prefix.
    """
    by_rep: dict[int, set[str]] = {}
    for name, replicate in pending:
        by_rep.setdefault(replicate, set()).add(name)
    out: dict[tuple[str, int], str] = {}
    for replicate in range(1, repetitions + 1):
        statuses = pending_statuses(by_rep.get(replicate, set()), tasks)
        for task in tasks:
            out[(task, replicate)] = statuses[task]
    return out


def newest_result_paths(
    variant_dir: Path, tasks: list[str]
) -> dict[tuple[str, int], Path]:
    """Newest result.json per (task, replicate) from one variant enumeration.

    One walk plus one parse per result.json serves all C lookups: Theta(F + C)
    instead of Theta(C x F). Selection mirrors latest_result_path exactly:
    newest mtime wins, ties keep the first path in sorted order.
    """
    wanted = set(tasks)
    best: dict[tuple[str, int], tuple[int, Path]] = {}
    _, entries = scan_variant(variant_dir, parse_results=True)
    for path, task, stamp, replicate, _data in entries:
        if task not in wanted:
            continue
        trial = (task, replicate)
        prev = best.get(trial)
        if prev is None or stamp > prev[0]:
            best[trial] = (stamp, path)
    return {trial: path for trial, (_, path) in best.items()}


def latest_result_path(jobs_root: Path, variant: str, task: str) -> Path | None:
    """Newest trial result.json under jobs_root/<variant> for one task.

    Mirrors the collect_rows selection (trial-dir check, task_name from
    result.json with the trial dir name as fallback, newest mtime wins
    across replicates) without parsing any trial's agent logs. Newest
    mtime wins; ties keep the first path in sorted order.
    """
    best: tuple[int, Path] | None = None
    _, entries = scan_variant(jobs_root / variant, parse_results=True)
    for path, name, stamp, _replicate, _data in entries:
        if name != task:
            continue
        if best is None or stamp > best[0]:
            best = (stamp, path)
    return best[1] if best is not None else None


def collect_rows(jobs_root: Path) -> list[dict[str, Any]]:
    """One row per (variant, task, replicate) under jobs_root.

    jobs_root is `<run>/jobs` for a roastmyharness run.
    """
    selected: dict[tuple[str, str, int], tuple[int, dict[str, Any]]] = {}
    jobs = jobs_root
    if not jobs.is_dir():
        return []
    for variant_dir in sorted(jobs.iterdir()):
        if not variant_dir.is_dir():
            continue
        _, entries = scan_variant(variant_dir, parse_results=True)
        for result_path, _task, stamp, replicate, _data in entries:
            row = trial_row(result_path, variant_dir.name, replicate=replicate)
            if not row:
                continue
            task_id = str(row.get("task") or result_path.parent.name)
            key = (variant_dir.name, task_id, replicate)
            if key not in selected or stamp >= selected[key][0]:
                selected[key] = (stamp, row)
    return [
        row
        for _, row in sorted(
            selected.values(),
            key=lambda item: (item[1]["variant"], item[1]["task"], item[1]["replicate"]),
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
    (variant, task, replicate) matches collect_rows. Legacy tuple entries
    from the earlier result-mtime cache refold once, then migrate to the
    new shape.
    """
    from roast_my_harness.telemetry.result import trial_row_cached

    folded_count = 0
    reused = 0
    if not jobs_root.is_dir():
        known.clear()
        return [], known, 0, 0
    seen: set[str] = set()
    per_path: dict[str, dict[str, Any]] = {}
    selected: dict[tuple[str, str, int], tuple[int, dict]] = {}
    for variant_dir in sorted(jobs_root.iterdir()):
        if not variant_dir.is_dir():
            continue
        _, entries = scan_variant(variant_dir, parse_results=True)
        for result_path, _task, _stamp, replicate, _data in entries:
            key = str(result_path)
            seen.add(key)
            cached = known.get(key)
            if not isinstance(cached, dict):
                cached = None
            row, updated, folded = trial_row_cached(
                result_path, variant_dir.name, cached, replicate=replicate
            )
            per_path[key] = updated
            if folded > 0:
                folded_count += 1
            else:
                reused += 1
            if row is None:
                continue
            task_id = str(row.get("task") or result_path.parent.name)
            vtask = (variant_dir.name, task_id, replicate)
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
            selected.values(),
            key=lambda item: (item[1]["variant"], item[1]["task"], item[1]["replicate"]),
        )
    ]
    return rows, known, folded_count, reused
