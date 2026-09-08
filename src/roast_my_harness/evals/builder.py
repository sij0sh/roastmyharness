"""Host-side validation for a custom-eval workspace.

The EvalBuilder agent may draft tasks and validators, but only the host
may freeze them: ``validate_workspace`` checks the full authoring
contract on disk (descriptor, capability map, rationale, tasks, critic
checklist, fixture self-tests) and refuses to launch unless every step
is complete. All builder writes go through ``write_text_sandboxed`` so
a faulty builder can never escape the eval workspace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from roast_my_harness.errors import SpecError
from roast_my_harness.evals.descriptor import EvalDescriptor, load_descriptor
from roast_my_harness.evals.selftest import run_selftests
from roast_my_harness.tasks.discover import discover_tasks, is_task_dir

CAPABILITY_MAP_FILENAME = "capability-map.json"
RATIONALE_FILENAME = "rationale.md"
CRITIC_FILENAME = "critic.json"
LEGACY_TASKS_DIRNAME = "tasks"


@dataclass(frozen=True)
class WorkspaceReport:
    """Result of validating one eval workspace."""

    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    eval_id: str | None = None
    revision: str | None = None
    task_count: int = 0
    fixture_count: int = 0


@dataclass
class _Accumulator:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def safe_join(root: Path, rel: str | Path) -> Path:
    """Join an untrusted relative path; raises SpecError on escape.

    The EvalBuilder writes only inside its workspace: absolute paths
    and ``..`` escapes are refused rather than resolved.
    """
    root = Path(root).expanduser().resolve()
    rel_path = Path(str(rel))
    if rel_path.is_absolute():
        raise SpecError(f"builder write refused: absolute path {rel!s} escapes {root}")
    joined = (root / rel_path).resolve()
    if joined != root and root not in joined.parents:
        raise SpecError(f"builder write refused: {rel!s} escapes {root}")
    return joined


def write_text_sandboxed(root: Path, rel: str | Path, content: str) -> Path:
    """Write a file inside the eval workspace; raises on escape."""
    path = safe_join(root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _task_root_for(root: Path) -> Path:
    """Task root for a workspace: the root, or its legacy tasks/ subdir."""
    if any(is_task_dir(child) for child in _direct_subdirs(root)):
        return root
    legacy = root / LEGACY_TASKS_DIRNAME
    if legacy.is_dir() and any(is_task_dir(child) for child in _direct_subdirs(legacy)):
        return legacy
    return root


def _direct_subdirs(path: Path) -> list[Path]:
    try:
        return [p for p in path.iterdir() if p.is_dir()]
    except OSError:
        return []


def _load_json(path: Path, acc: _Accumulator) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        acc.errors.append(f"{path.name} unreadable: {error}")
        return None
    if not isinstance(raw, dict):
        acc.errors.append(f"{path.name} must be a JSON mapping")
        return None
    return raw


def _check_capability_map(root: Path, task_ids: set[str], acc: _Accumulator) -> None:
    path = root / CAPABILITY_MAP_FILENAME
    if not path.is_file():
        acc.errors.append(f"{CAPABILITY_MAP_FILENAME} missing")
        return
    raw = _load_json(path, acc)
    if raw is None:
        return
    capabilities = raw.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        acc.errors.append(f"{CAPABILITY_MAP_FILENAME} needs a non-empty capabilities list")
        return
    for index, entry in enumerate(capabilities):
        where = f"{CAPABILITY_MAP_FILENAME} capabilities[{index}]"
        if isinstance(entry, str):
            if not entry.strip():
                acc.errors.append(f"{where} must not be empty")
            continue
        if not isinstance(entry, dict):
            acc.errors.append(f"{where} must be a mapping or string")
            continue
        if not entry.get("id") or not entry.get("description"):
            acc.errors.append(f"{where} needs id and description")
        refs = entry.get("tasks", [])
        if isinstance(refs, str):
            refs = [refs]
        if isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, str) and ref not in task_ids:
                    acc.errors.append(f"{where} maps to unknown task {ref!r}")
        if "TODO" in json.dumps(entry):
            acc.warnings.append(f"{where} still carries a TODO marker")


def _check_rationale(root: Path, acc: _Accumulator) -> None:
    path = root / RATIONALE_FILENAME
    if not path.is_file():
        acc.errors.append(f"{RATIONALE_FILENAME} missing")
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        acc.errors.append(f"{RATIONALE_FILENAME} unreadable: {error}")
        return
    if "TODO" in text:
        acc.errors.append(f"{RATIONALE_FILENAME} still has TODO sections: finish authoring first")


def _check_critic(root: Path, acc: _Accumulator) -> None:
    path = root / "validation" / CRITIC_FILENAME
    if not path.is_file():
        acc.errors.append(f"validation/{CRITIC_FILENAME} missing")
        return
    raw = _load_json(path, acc)
    if raw is None:
        return
    checks = raw.get("checks")
    if not isinstance(checks, list) or not checks:
        acc.errors.append(f"validation/{CRITIC_FILENAME} needs a non-empty checks list")
    verdict = raw.get("verdict")
    if verdict != "pass":
        acc.errors.append(
            f"validation/{CRITIC_FILENAME} verdict is {verdict!r}: "
            "the critic must pass before the eval freezes"
        )


def validate_workspace(root: Path | str) -> WorkspaceReport:
    """Validate a full eval workspace; never raises, reports instead."""
    root = Path(root).expanduser().resolve()
    acc = _Accumulator()
    if not root.is_dir():
        return WorkspaceReport(ok=False, errors=(f"eval workspace not found: {root}",))
    try:
        descriptor: EvalDescriptor | None = load_descriptor(_task_root_for(root))
        if descriptor is None:
            descriptor = load_descriptor(root)
    except SpecError as error:
        acc.errors.append(str(error))
        descriptor = None
    if descriptor is None and not any("eval" in e for e in acc.errors):
        acc.errors.append("eval.toml missing or invalid beside the task root")
    task_root = _task_root_for(root)
    try:
        tasks = discover_tasks(task_root, ["*"], [])
        task_ids = {t.task_id for t in tasks}
    except SpecError as error:
        acc.errors.append(str(error))
        tasks = []
        task_ids = set()
    for task in tasks:
        if not (task.path / "instruction.md").is_file():
            acc.errors.append(f"task {task.task_id} needs instruction.md")
    _check_capability_map(
        root if (root / CAPABILITY_MAP_FILENAME).exists() else task_root, task_ids, acc
    )
    _check_rationale(root if (root / RATIONALE_FILENAME).exists() else task_root, acc)
    _check_critic(root if (root / "validation").exists() else task_root, acc)
    fixture_count = 0
    if descriptor is not None and tasks:
        try:
            result = run_selftests(task_root, descriptor)
        except SpecError as error:
            acc.errors.append(str(error))
        else:
            fixture_count = result.evaluated
            for failure in result.failures:
                acc.errors.append(f"self-test {failure.fixture}: {failure.message}")
    return WorkspaceReport(
        ok=not acc.errors,
        errors=tuple(acc.errors),
        warnings=tuple(acc.warnings),
        eval_id=descriptor.id if descriptor is not None else None,
        revision=descriptor.revision if descriptor is not None else None,
        task_count=len(tasks),
        fixture_count=fixture_count,
    )


__all__ = [
    "CAPABILITY_MAP_FILENAME",
    "CRITIC_FILENAME",
    "RATIONALE_FILENAME",
    "WorkspaceReport",
    "safe_join",
    "validate_workspace",
    "write_text_sandboxed",
]
