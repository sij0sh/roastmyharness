"""Wizard context: one bridge payload for the Pi experiment wizard.

The extension calls this after the user picks a model and thinking level.
It reports the discovered tasks under a task root, the curated Luna/GLM
suites filtered to those tasks, and the historic control pool for the
exact model/thinking combo. Curated lists come from suites.json; when no
copy sits beside the task root the bundled copy fills in, and an empty
mapping hides the curated options instead of failing the wizard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from roast_my_harness.errors import RoastMyHarnessError
from roast_my_harness.paths import database_path
from roast_my_harness.setup import bundled_root, repo_root
from roast_my_harness.store.repository import Repository
from roast_my_harness.tasks.discover import discover_tasks

SUITE_KEYS = ("luna_signal", "luna_confirmation", "glm_signal", "glm_confirmation")


def find_suites_doc(task_root: Path) -> dict[str, Any]:
    """Load the nearest suites.json; {} when none is on disk."""
    candidates = [
        task_root / "suites.json",
        task_root.parent / "suites.json",
    ]
    for base in (bundled_root(), repo_root()):
        if base is not None:
            candidates.append(base / "tasks" / "deepswe" / "suites.json")
    for path in candidates:
        if path.is_file():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
    return {}


def curated_suites(task_root: Path, discovered: set[str]) -> dict[str, list[str]]:
    """Curated suite id lists filtered to tasks present on disk."""
    doc = find_suites_doc(task_root)
    raw = doc.get("suites", {}) if isinstance(doc, dict) else {}
    suites: dict[str, list[str]] = {}
    for family in ("luna", "glm"):
        entry = raw.get(family, {})
        for part in ("signal", "confirmation"):
            key = f"{family}_{part}"
            tasks = entry.get(part, [])
            suites[key] = [t for t in tasks if t in discovered] if isinstance(tasks, list) else []
    return suites


def historic_pool(model: str, thinking: str) -> dict[str, int]:
    """Resolved control-trial runs per task for one model/thinking combo."""
    db_path = database_path()
    if not db_path.is_file():
        return {}
    repo = Repository(db_path)
    try:
        return repo.historic_control_runs(model, thinking)
    finally:
        repo.close()


def wizard_context(task_root: Path, model: str, thinking: str) -> dict[str, Any]:
    """Discover tasks, curate suites, and report historic control depth."""
    try:
        tasks = discover_tasks(task_root, ["*"], [])
    except RoastMyHarnessError as error:
        return {"ok": False, "error": {"code": "no_tasks", "message": str(error)}}
    discovered = [t.task_id for t in tasks]
    present = set(discovered)
    pool = historic_pool(model, thinking)
    historic_ids = sorted(t for t in pool if t in present)
    return {
        "ok": True,
        "task_root": str(task_root.expanduser().resolve()),
        "discovered": discovered,
        "suites": curated_suites(task_root, present),
        "historic": {
            "count": len(historic_ids),
            "task_ids": historic_ids,
            "runs": {t: pool[t] for t in historic_ids},
        },
    }
