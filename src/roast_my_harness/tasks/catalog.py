"""Benchmark catalog: task metadata outside task directories.

Task content hashes walk every file under each task dir, so duration or
difficulty labels stored inside a task dir would invalidate run identity
on every label edit. Labels live here, in
one catalog file per benchmark root, with their own catalog_hash in run
provenance. Unknown tasks read as unlabeled; labels without a recorded
basis are rejected.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from roast_my_harness.errors import SpecError

CATALOG_FILENAME = "catalog.toml"
CATALOG_VERSION = 1

DURATIONS = ("fast", "long")
DIFFICULTIES = ("easy", "medium", "hard")


@dataclass(frozen=True)
class TaskMeta:
    """One task's labels; every label needs its measurement basis."""

    duration: str | None = None
    difficulty: str | None = None
    smoke: bool = False
    estimated_minutes: int | None = None
    basis_revision: str | None = None
    basis_samples: int | None = None
    basis_date: str | None = None
    basis: str = ""


@dataclass(frozen=True)
class Preset:
    label: str
    tasks: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkCatalog:
    """Parsed catalog plus its content hash for run provenance."""

    path: Path
    benchmark: str
    revision: str
    source: str = ""
    presets: dict[str, Preset] = field(default_factory=dict)
    tasks: dict[str, TaskMeta] = field(default_factory=dict)
    sha256: str = ""


def _parse_task_meta(task_id: str, raw: object) -> TaskMeta:
    if not isinstance(raw, dict):
        raise SpecError(f"catalog task {task_id!r} must be a mapping")
    unknown = set(raw) - {
        "duration", "difficulty", "smoke", "estimated_minutes",
        "basis_revision", "basis_samples", "basis_date", "basis",
    }
    if unknown:
        raise SpecError(
            f"catalog task {task_id!r} has unknown fields: {sorted(unknown)}"
        )
    duration = raw.get("duration")
    if duration is not None and duration not in DURATIONS:
        raise SpecError(
            f"catalog task {task_id!r} duration must be one of {DURATIONS}"
        )
    difficulty = raw.get("difficulty")
    if difficulty is not None and difficulty not in DIFFICULTIES:
        raise SpecError(
            f"catalog task {task_id!r} difficulty must be one of {DIFFICULTIES}"
        )
    labeled = (
        duration is not None
        or difficulty is not None
        or raw.get("smoke", False)
        or raw.get("estimated_minutes") is not None
    )
    if labeled and not raw.get("basis_revision"):
        raise SpecError(
            f"catalog task {task_id!r} is labeled without basis_revision; "
            "labels need their measurement basis recorded"
        )
    smoke = raw.get("smoke", False)
    if not isinstance(smoke, bool):
        raise SpecError(f"catalog task {task_id!r} smoke must be true/false")
    return TaskMeta(
        duration=duration,
        difficulty=difficulty,
        smoke=smoke,
        estimated_minutes=raw.get("estimated_minutes"),
        basis_revision=raw.get("basis_revision"),
        basis_samples=raw.get("basis_samples"),
        basis_date=raw.get("basis_date"),
        basis=str(raw.get("basis", "")),
    )


def load_catalog(root: Path) -> BenchmarkCatalog | None:
    """Parse root/catalog.toml; None when the benchmark has no catalog."""
    path = Path(root) / CATALOG_FILENAME
    if not path.is_file():
        return None
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SpecError(f"cannot read benchmark catalog {path}: {e}") from e
    if not isinstance(raw, dict):
        raise SpecError(f"invalid benchmark catalog {path}: expected a mapping")
    if raw.get("catalog_version", 1) != CATALOG_VERSION:
        raise SpecError(
            f"unsupported catalog_version {raw.get('catalog_version')} in {path}, "
            f"expected {CATALOG_VERSION}"
        )
    presets: dict[str, Preset] = {}
    raw_presets = raw.get("presets", {})
    if not isinstance(raw_presets, dict):
        raise SpecError(f"catalog {path} presets must be a mapping")
    for name, entry in raw_presets.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("tasks"), list):
            raise SpecError(f"catalog preset {name!r} needs a tasks list")
        if not entry["tasks"] or not all(isinstance(t, str) for t in entry["tasks"]):
            raise SpecError(f"catalog preset {name!r} tasks must be non-empty strings")
        presets[str(name)] = Preset(
            label=str(entry.get("label", name)), tasks=tuple(entry["tasks"])
        )
    raw_tasks = raw.get("tasks", {})
    if not isinstance(raw_tasks, dict):
        raise SpecError(f"catalog {path} tasks must be a mapping")
    tasks = {
        str(task_id): _parse_task_meta(str(task_id), entry)
        for task_id, entry in raw_tasks.items()
    }
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return BenchmarkCatalog(
        path=path,
        benchmark=str(raw.get("benchmark", path.parent.name)),
        revision=str(raw.get("benchmark_revision", "")),
        source=str(raw.get("source", "")),
        presets=presets,
        tasks=tasks,
        sha256=digest,
    )


def catalog_info(root: Path) -> tuple[str | None, str | None]:
    """(revision, sha256) for run provenance; (None, None) without catalog."""
    catalog = load_catalog(root)
    if catalog is None:
        return None, None
    return catalog.revision, catalog.sha256
