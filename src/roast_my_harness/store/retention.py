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

DEFAULT_RETENTION_ENABLED = True
DEFAULT_RETENTION_MAX_BYTES = 5 * 1024**3

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
    source: str = "defaults"


@dataclass
class RetentionResult:
    total_before: int
    total_after: int
    max_bytes: int
    deleted: list[str] = field(default_factory=list)
    freed_bytes: int = 0
    skipped_active: str | None = None


def load_storage_settings() -> StorageSettings:
    """Resolve storage config. Precedence: env, then config file, then defaults."""
    root = data_dir()
    runs = runs_root()
    db = database_path()
    enabled = DEFAULT_RETENTION_ENABLED
    max_bytes = DEFAULT_RETENTION_MAX_BYTES
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
    return StorageSettings(
        data_dir=root,
        runs_dir=runs,
        db_path=db,
        retention_enabled=enabled,
        retention_max_bytes=max_bytes,
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
    if active.retention_max_bytes <= 0:
        return None
    try:
        return enforce_retention(
            active.runs_dir,
            active.retention_max_bytes,
            exclude={exclude} if exclude else None,
            db_path=active.db_path,
            progress=progress,
        )
    except OSError:
        return None
