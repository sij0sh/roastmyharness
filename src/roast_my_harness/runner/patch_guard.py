"""Empty-patch and artifact-failure classification for trial directories.

A zero-byte ``model.patch`` must never score 0 when there is evidence the
agent mutated sources, and artifact-copy failures must read as
infrastructure errors rather than quality signal. Both flow into reconcile
cells and report rows as ``exception_type``, so the existing statistics
(which exclude exception rows from agent outcomes) automatically drop them
instead of averaging them in as failures.

Evidence, strongest first:

1. pier's ``artifacts/manifest.json`` records a ``failed`` download for
   ``model.patch`` (typically a permissions problem copying out of the
   container) -> ``INFRA_ARTIFACT_COPY``.
2. the collect hook's ``artifacts/worktree-status.txt`` shows a dirty tree
   at capture time while the patch is empty -> ``INVALID_EMPTY_PATCH``.
   The new collect command folds every dirty-tree state into the patch, so
   an empty patch beside a dirty tree means collection itself failed.
3. the agent trajectory shows file-mutation tool calls (legacy runs that
   predate the status file) -> ``INVALID_EMPTY_PATCH``.

A missing manifest or status file is unknown, not failure: old trials
predate both files and must keep grading exactly as before unless the
trajectory proves mutations happened.
"""

from __future__ import annotations

import json
from pathlib import Path

INVALID_EMPTY_PATCH = "INVALID_EMPTY_PATCH"
INFRA_ARTIFACT_COPY = "INFRA_ARTIFACT_COPY"

# Artifact files the classifier reads beside model.patch.
PATCH_FILENAME = "model.patch"
MANIFEST_FILENAME = "manifest.json"
WORKTREE_STATUS_FILENAME = "worktree-status.txt"


def step_dirs(trial_dir: Path) -> list[Path]:
    """Sorted per-step dirs of a staged (multi-step pier) trial.

    Pier relocates each step's agent//verifier//artifacts/ content under
    steps/<name>/ as the trial advances, rmdir-ing the emptied trial roots.
    Step names sort in execution order by task-authoring convention.
    """
    steps = trial_dir / "steps"
    if not steps.is_dir():
        return []
    return sorted([p for p in steps.iterdir() if p.is_dir()], key=lambda p: p.name)


def is_stepped_trial(trial_dir: Path) -> bool:
    """True once a staged trial has relocated its first step."""
    return bool(step_dirs(trial_dir))


def has_trial_logs(trial_dir: Path) -> bool:
    """Single-step layout or stepped layout (completed or mid-relocation)."""
    if (trial_dir / "agent").is_dir() and (trial_dir / "verifier").is_dir():
        return True
    return is_stepped_trial(trial_dir)


def _artifact_beside(trial_dir: Path, filename: str) -> Path:
    """Artifact path, falling back to the last completed step.

    Staged trials move per-step artifacts/ under steps/<name>/. The last
    step's capture is the trial-grade one (cumulative diff vs base).
    """
    direct = trial_dir / "artifacts" / filename
    if direct.exists():
        return direct
    steps = step_dirs(trial_dir)
    if steps:
        return steps[-1] / "artifacts" / filename
    return direct

# Upper bound for trajectory scans; trial event logs are small, but a corrupt
# or runaway file must not stall reconciliation.
_MAX_SCAN_BYTES = 32 * 1024 * 1024

# Conservative exact-match set of file-mutating tool names seen in pi-family
# event streams (pi-events.jsonl toolName) and ATIF trajectories
# (tool_calls[].function_name). Reads, searches, and shell calls are
# deliberately excluded: only an explicit write-shaped call counts, so a
# read-only agent that changed nothing keeps its legitimate fail.
MUTATION_TOOL_NAMES = frozenset(
    {
        "write",
        "edit",
        "apply_patch",
        "apply_edit",
        "multiedit",
        "str_replace",
        "create_file",
        "write_file",
        "edit_file",
        "delete_file",
        "new_file",
    }
)


def patch_size_bytes(trial_dir: Path) -> int | None:
    """Size of artifacts/model.patch, or None when missing/unreadable."""
    try:
        return _artifact_beside(trial_dir, PATCH_FILENAME).stat().st_size
    except OSError:
        return None


def manifest_model_patch_status(trial_dir: Path) -> str | None:
    """The pier artifact-manifest status for model.patch, if recorded.

    Returns the entry status ("ok", "failed", "empty") or None when the
    manifest or its model.patch entry is absent (unknown, not failure).
    """
    try:
        manifest = json.loads(_artifact_beside(trial_dir, MANIFEST_FILENAME).read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in ("destination", "source"):
            value = entry.get(key)
            if isinstance(value, str) and value.rstrip("/").endswith(PATCH_FILENAME):
                status = entry.get("status")
                return str(status) if isinstance(status, str) else None
    return None


def worktree_dirty(trial_dir: Path) -> bool | None:
    """Whether the collect-time tree state shows uncollected changes.

    Any porcelain line (tracked modification, staged change, or untracked
    file) means the tree was dirty when the hook ran. Returns None when the
    status file is absent (predates the new collect command: unknown).
    """
    try:
        text = _artifact_beside(trial_dir, WORKTREE_STATUS_FILENAME).read_text()
    except (OSError, UnicodeDecodeError):
        return None
    return any(line.strip() for line in text.splitlines())


def agent_mutation_evidence(trial_dir: Path) -> bool:
    """True when the agent logs show an explicit file-mutation tool call."""
    candidates = [trial_dir / "agent"]
    candidates.extend(step / "agent" for step in step_dirs(trial_dir))
    for agent_dir in candidates:
        if _events_show_mutation(agent_dir / "pi-events.jsonl"):
            return True
        if _trajectory_shows_mutation(agent_dir / "trajectory.json"):
            return True
    return False


def _is_mutation_tool(name: object) -> bool:
    return isinstance(name, str) and name.strip().lower() in MUTATION_TOOL_NAMES


def _bounded_lines(path: Path) -> list[str]:
    try:
        if path.stat().st_size > _MAX_SCAN_BYTES:
            return []
        return path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def _events_show_mutation(path: Path) -> bool:
    for line in _bounded_lines(path):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") != "tool_execution_start":
            continue
        if _is_mutation_tool(event.get("toolName")):
            return True
    return False


def _trajectory_shows_mutation(path: Path) -> bool:
    try:
        if path.stat().st_size > _MAX_SCAN_BYTES:
            return False
        trajectory = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False
    steps = trajectory.get("steps") if isinstance(trajectory, dict) else None
    if not isinstance(steps, list):
        return False
    for step in steps:
        if not isinstance(step, dict):
            continue
        calls = step.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if isinstance(call, dict) and _is_mutation_tool(call.get("function_name")):
                return True
    return False


def classify_empty_patch(trial_dir: Path) -> str | None:
    """Harness label for a terminal zero-reward trial with an empty patch.

    Returns a harness exception label, or None when the empty patch is a
    legitimate no-op signal (clean tree, no mutation evidence, artifacts
    copied fine) and the trial should keep its fail/0 grade.
    """
    size = patch_size_bytes(trial_dir)
    if size:
        return None
    if manifest_model_patch_status(trial_dir) == "failed":
        return INFRA_ARTIFACT_COPY
    if worktree_dirty(trial_dir):
        return INVALID_EMPTY_PATCH
    if agent_mutation_evidence(trial_dir):
        return INVALID_EMPTY_PATCH
    return None
