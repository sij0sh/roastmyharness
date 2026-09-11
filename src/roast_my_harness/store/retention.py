"""Disk retention for run outputs. Oldest-first pruning under a size cap."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from roast_my_harness.paths import config_path, data_dir, database_path, runs_root

RETENTION_ENABLED_ENV = "ROAST_MY_HARNESS_RETENTION"
RETENTION_MAX_ENV = "ROAST_MY_HARNESS_RETENTION_MAX_SIZE"
RETENTION_MAX_BYTES_ENV = "ROAST_MY_HARNESS_RETENTION_MAX_BYTES"
VARIANT_MAX_ENV = "ROAST_MY_HARNESS_RETENTION_VARIANT_MAX_SIZE"
VARIANT_MAX_BYTES_ENV = "ROAST_MY_HARNESS_RETENTION_VARIANT_MAX_BYTES"
CONTROL_MAX_ENV = "ROAST_MY_HARNESS_RETENTION_CONTROL_MAX_SIZE"
CONTROL_MAX_BYTES_ENV = "ROAST_MY_HARNESS_RETENTION_CONTROL_MAX_BYTES"
CONTROL_KEEP_ENV = "ROAST_MY_HARNESS_RETENTION_CONTROL_KEEP"
CONTROL_RETENTION_ENV = "ROAST_MY_HARNESS_RETENTION_CONTROL_RETENTION"

DEFAULT_RETENTION_ENABLED = True
DEFAULT_RETENTION_MAX_BYTES = 5 * 1024**3
DEFAULT_VARIANT_MAX_BYTES = 3 * 1024**3
DEFAULT_CONTROL_MAX_BYTES = 5 * 1024**3
DEFAULT_CONTROL_KEEP = 4
DEFAULT_CONTROL_RETENTION = False

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?b?)?\s*$", re.IGNORECASE)
_UNIT_MULTIPLIER = {"": 1, "b": 1, "k": 1024, "kb": 1024, "m": 1024**2,
                    "mb": 1024**2, "g": 1024**3, "gb": 1024**3,
                    "t": 1024**4, "tb": 1024**4}
_FALSY = {"0", "false", "no", "off", "disabled", ""}
_TRUTHY = {"1", "true", "yes", "on", "enabled"}


def parse_size(value: str | int | float) -> int:
    """Parse "500mb", "1.5 GB", or a bare byte count into bytes."""
    if isinstance(value, (int, float)):
        result = int(value)
        if result < 0:
            raise ValueError(f"size must be >= 0, got {value!r}")
        return result
    match = _SIZE_RE.match(str(value))
    if not match:
        raise ValueError(
            f"invalid size {value!r}; use bytes or a suffix like 500MB, 2GB"
        )
    number, unit = match.groups()
    multiplier = _UNIT_MULTIPLIER[(unit or "").lower()]
    result = int(float(number) * multiplier)
    if result < 0:
        raise ValueError(f"size must be >= 0, got {value!r}")
    return result


def format_size(num_bytes: int) -> str:
    """Human size for progress logs (e.g. 1.5GB, 500.0MB)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{int(size)}B"


def parse_enabled(value: str | bool) -> bool:
    """Parse a truthy/falsy retention toggle."""
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in _FALSY:
        return False
    if normalized in _TRUTHY:
        return True
    raise ValueError(
        f"invalid retention toggle {value!r}; use true/false, 1/0, on/off"
    )


@dataclass
class StorageSettings:
    data_dir: Path
    runs_dir: Path
    db_path: Path
    retention_enabled: bool = DEFAULT_RETENTION_ENABLED
    retention_max_bytes: int = DEFAULT_RETENTION_MAX_BYTES
    variant_max_bytes: int = DEFAULT_VARIANT_MAX_BYTES
    control_max_bytes: int = DEFAULT_CONTROL_MAX_BYTES
    control_keep: int = DEFAULT_CONTROL_KEEP
    control_retention: bool = DEFAULT_CONTROL_RETENTION
    source: str = "defaults"


@dataclass
class RetentionResult:
    total_before: int
    total_after: int
    max_bytes: int
    deleted: list[str] = field(default_factory=list)
    freed_bytes: int = 0
    skipped_active: str | None = None
    control_trials: list[str] = field(default_factory=list)


def load_storage_settings() -> StorageSettings:
    """Resolve storage config. Precedence: env, then config file, then defaults."""
    root = data_dir()
    runs = runs_root()
    db = database_path()
    enabled = DEFAULT_RETENTION_ENABLED
    max_bytes = DEFAULT_RETENTION_MAX_BYTES
    variant_max = DEFAULT_VARIANT_MAX_BYTES
    control_max = DEFAULT_CONTROL_MAX_BYTES
    control_keep = DEFAULT_CONTROL_KEEP
    control_retention = DEFAULT_CONTROL_RETENTION
    source = "defaults"
    cfg = config_path()
    if cfg.is_file():
        try:
            import tomllib

            raw = tomllib.loads(cfg.read_text())
            if isinstance(raw, dict):
                retention = raw.get("retention", {})
                storage = raw.get("storage", {})
                if isinstance(storage, dict) and storage.get("runs_dir"):
                    if not os.environ.get("ROAST_MY_HARNESS_RUNS_DIR"):
                        runs = Path(str(storage["runs_dir"])).expanduser()
                if isinstance(retention, dict):
                    if "enabled" in retention:
                        enabled = bool(retention["enabled"])
                    for key in ("max_size", "max_bytes", "max_mb"):
                        if key in retention and retention[key] is not None:
                            raw_max = retention[key]
                            if key == "max_mb":
                                max_bytes = int(float(raw_max) * 1024**2)
                            elif isinstance(raw_max, (int, float)):
                                max_bytes = int(raw_max)
                            else:
                                max_bytes = parse_size(str(raw_max))
                            break
                    for key in ("variant_max_size", "variant_max_bytes"):
                        if key in retention and retention[key] is not None:
                            raw_variant = retention[key]
                            variant_max = (
                                int(raw_variant)
                                if isinstance(raw_variant, (int, float))
                                else parse_size(str(raw_variant))
                            )
                            break
                    else:
                        if max_bytes != DEFAULT_RETENTION_MAX_BYTES:
                            variant_max = max_bytes
                    for key in ("control_max_size", "control_max_bytes"):
                        if key in retention and retention[key] is not None:
                            raw_control = retention[key]
                            control_max = (
                                int(raw_control)
                                if isinstance(raw_control, (int, float))
                                else parse_size(str(raw_control))
                            )
                            break
                    for key in ("control_keep", "control_keep_per_group"):
                        if key in retention and retention[key] is not None:
                            control_keep = max(0, int(retention[key]))
                            break
                    if "control_retention" in retention:
                        control_retention = bool(retention["control_retention"])
                    source = f"config:{cfg}"
        except (OSError, ValueError):
            pass
    toggle = os.environ.get(RETENTION_ENABLED_ENV)
    if toggle is not None:
        enabled = parse_enabled(toggle)
        source = f"env:{RETENTION_ENABLED_ENV}"
    max_raw = os.environ.get(RETENTION_MAX_ENV)
    if max_raw is None:
        max_raw = os.environ.get(RETENTION_MAX_BYTES_ENV)
    if max_raw is not None:
        max_bytes = parse_size(max_raw.strip())
        source = f"env:{RETENTION_MAX_ENV}"
    variant_raw = os.environ.get(VARIANT_MAX_ENV)
    if variant_raw is None:
        variant_raw = os.environ.get(VARIANT_MAX_BYTES_ENV)
    if variant_raw is not None:
        variant_max = parse_size(variant_raw.strip())
        source = f"env:{VARIANT_MAX_ENV}"
    elif max_raw is not None:
        variant_max = max_bytes
    control_raw = os.environ.get(CONTROL_MAX_ENV)
    if control_raw is None:
        control_raw = os.environ.get(CONTROL_MAX_BYTES_ENV)
    if control_raw is not None:
        control_max = parse_size(control_raw.strip())
        source = f"env:{CONTROL_MAX_ENV}"
    keep_raw = os.environ.get(CONTROL_KEEP_ENV)
    if keep_raw is not None:
        control_keep = max(0, int(keep_raw.strip()))
        source = f"env:{CONTROL_KEEP_ENV}"
    retention_raw = os.environ.get(CONTROL_RETENTION_ENV)
    if retention_raw is not None:
        control_retention = parse_enabled(retention_raw.strip())
        source = f"env:{CONTROL_RETENTION_ENV}"
    return StorageSettings(
        data_dir=root,
        runs_dir=runs,
        db_path=db,
        retention_enabled=enabled,
        retention_max_bytes=max_bytes,
        variant_max_bytes=variant_max,
        control_max_bytes=control_max,
        control_keep=control_keep,
        control_retention=control_retention,
        source=source,
    )


def dir_size(path: Path) -> int:
    """Sum regular file sizes under path without following symlinks."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path, followlinks=False):
            for name in files:
                try:
                    entry = Path(root) / name
                    if entry.is_symlink():
                        continue
                    total += entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _candidate_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def enforce_retention(
    runs_dir: Path,
    max_bytes: int,
    *,
    exclude: set[str] | None = None,
    db_path: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> RetentionResult:
    """Delete oldest run dirs until the runs tree fits max_bytes.

    Never deletes names in exclude (the run about to start). When db_path
    is given, rows for pruned experiments are removed so records never
    point at a deleted dir.
    """
    excluded = set(exclude or set())
    result = RetentionResult(total_before=0, total_after=0, max_bytes=max_bytes)
    if not runs_dir.is_dir():
        return result
    candidates = [p for p in runs_dir.iterdir() if p.is_dir() and not p.is_symlink()]
    sizes = {p: dir_size(p) for p in candidates}
    total = sum(sizes.values())
    result.total_before = total
    result.total_after = total
    if total <= max_bytes:
        return result
    ordered = sorted(candidates, key=_candidate_mtime)
    pruned_ids: list[str] = []
    for candidate in ordered:
        if total <= max_bytes:
            break
        if candidate.name in excluded:
            result.skipped_active = candidate.name
            continue
        freed = sizes.get(candidate, 0)
        try:
            shutil.rmtree(candidate, ignore_errors=True)
        except OSError:
            continue
        try:
            if candidate.exists():
                continue
        except OSError:
            pass
        total -= freed
        pruned_ids.append(candidate.name)
        result.freed_bytes += freed
        if progress is not None:
            progress(f"retention: pruned {candidate.name} ({format_size(freed)})")
    result.deleted = pruned_ids
    result.total_after = total
    if db_path is not None and pruned_ids:
        try:
            from roast_my_harness.store.repository import Repository

            repo = Repository(db_path)
            try:
                for experiment_id in pruned_ids:
                    repo.delete_experiment(experiment_id)
            finally:
                repo.close()
        except OSError:
            pass
    return result


def _control_bytes(run_dir: Path) -> int:
    """Disk use of one run's control arm; 0 when the run has none."""
    return dir_size(run_dir / "jobs" / "control")


def prune_control_groups(
    runs_dir: Path,
    db_path: Path,
    keep: int,
    *,
    exclude: set[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[str], int]:
    """Drop resolved control trials past keep per (task, model, thinking).

    Matches the historic-pool definition of reusable control data: only
    trials with a verdict count. Surplus rows lose their DB record and
    their trial dir; runs stay intact. Never raises: fail-open per trial.
    """
    excluded = set(exclude or set())
    if keep < 1:
        return [], 0
    if not db_path.is_file():
        return [], 0
    import json
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except OSError:
        return [], 0
    try:
        try:
            rows = conn.execute(
                "SELECT t.id, t.task_id, t.job_path, t.finished_at, "
                "e.spec_json, e.id AS exp FROM trials t "
                "JOIN experiments e ON e.id = t.experiment_id "
                "WHERE t.variant_id = 'control' AND t.resolved IS NOT NULL"
            ).fetchall()
        except Exception:
            return [], 0
        groups: dict[tuple[str, str, str], list] = {}
        for row in rows:
            if row["exp"] in excluded:
                continue
            try:
                stored = json.loads(row["spec_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            model = stored.get("model", {})
            full = (
                model
                if isinstance(model, str)
                else f"{model.get('provider', '')}/{model.get('id', '')}"
            )
            thinking = str(stored.get("thinking") or "")
            groups.setdefault((row["task_id"], full, thinking), []).append(row)
        pruned: list[str] = []
        freed = 0
        for members in groups.values():
            if len(members) <= keep:
                continue
            members.sort(key=lambda r: (r["finished_at"] or "", r["id"]))
            for stale in members[:-keep]:
                trial_id = stale["id"]
                job = stale["job_path"] or ""
                try:
                    trial_dir = Path(job).expanduser()
                    if (
                        job
                        and trial_dir.is_relative_to(runs_dir)
                        and "control" in trial_dir.parts
                        and trial_dir.is_dir()
                    ):
                        freed += dir_size(trial_dir)
                        shutil.rmtree(trial_dir, ignore_errors=True)
                except (OSError, ValueError):
                    pass
                try:
                    with conn:
                        conn.execute("DELETE FROM trials WHERE id = ?", (trial_id,))
                except Exception:
                    continue
                pruned.append(trial_id)
                if progress is not None:
                    progress(f"retention: pruned control trial {trial_id}")
        return pruned, freed
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _delete_run(run_dir: Path, db_path: Path | None) -> bool:
    try:
        shutil.rmtree(run_dir, ignore_errors=True)
    except OSError:
        return False
    try:
        if run_dir.exists():
            return False
    except OSError:
        pass
    if db_path is not None:
        try:
            from roast_my_harness.store.repository import Repository

            repo = Repository(db_path)
            try:
                repo.delete_experiment(run_dir.name)
            finally:
                repo.close()
        except OSError:
            pass
    return True


def enforce_split_retention(
    runs_dir: Path,
    *,
    variant_max_bytes: int,
    control_max_bytes: int,
    enforce_control_cap: bool,
    exclude: set[str] | None = None,
    db_path: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> RetentionResult:
    """Oldest-first whole-run pruning under split variant/control caps."""
    excluded = set(exclude or set())
    cap = variant_max_bytes + (control_max_bytes if enforce_control_cap else 0)
    result = RetentionResult(total_before=0, total_after=0, max_bytes=cap)
    if not runs_dir.is_dir():
        return result
    runs = [p for p in runs_dir.iterdir() if p.is_dir() and not p.is_symlink()]
    control_of = {r: _control_bytes(r) for r in runs}
    total_of = {r: dir_size(r) for r in runs}
    control_total = sum(control_of.values())
    total = sum(total_of.values())
    result.total_before = total
    result.total_after = total
    if enforce_control_cap and control_max_bytes > 0:
        for run in sorted(runs, key=_candidate_mtime):
            if control_total <= control_max_bytes:
                break
            if run.name in excluded or control_of.get(run, 0) <= 0:
                if run.name in excluded:
                    result.skipped_active = run.name
                continue
            if _delete_run(run, db_path):
                control_total -= control_of.get(run, 0)
                total -= total_of.get(run, 0)
                result.deleted.append(run.name)
                result.freed_bytes += total_of.get(run, 0)
                if progress is not None:
                    progress(f"retention: pruned {run.name}")
    if variant_max_bytes > 0:
        variant_total = total - control_total
        for run in sorted(runs, key=_candidate_mtime):
            if variant_total <= variant_max_bytes:
                break
            if run.name in excluded or run.name in result.deleted:
                if run.name in excluded:
                    result.skipped_active = run.name
                continue
            if _delete_run(run, db_path):
                control_total -= control_of.get(run, 0)
                total -= total_of.get(run, 0)
                variant_total = total - control_total
                result.deleted.append(run.name)
                result.freed_bytes += total_of.get(run, 0)
                if progress is not None:
                    progress(f"retention: pruned {run.name}")
    result.total_after = total
    return result


def enforce_storage_policy(
    *,
    exclude: str | None = None,
    progress: Callable[[str], None] | None = None,
    settings: StorageSettings | None = None,
) -> RetentionResult | None:
    """Load settings and prune when enabled. Never raises: fail-open."""
    try:
        active = settings or load_storage_settings()
    except (OSError, ValueError):
        return None
    if not active.retention_enabled:
        return None
    try:
        trials, trial_bytes = prune_control_groups(
            active.runs_dir,
            active.db_path,
            active.control_keep,
            exclude={exclude} if exclude else None,
            progress=progress,
        )
        result = enforce_split_retention(
            active.runs_dir,
            variant_max_bytes=active.variant_max_bytes,
            control_max_bytes=active.control_max_bytes,
            enforce_control_cap=not active.control_retention,
            exclude={exclude} if exclude else None,
            db_path=active.db_path,
            progress=progress,
        )
        result.control_trials = trials
        result.freed_bytes += trial_bytes
        result.total_before += trial_bytes
        return result
    except OSError:
        return None
