"""Scaffold a new custom-eval workspace: contract templates, empty gate.

``init_eval`` writes the authoring skeleton into the future task root:
eval.toml (scoring bar, judge disabled), capability-map/rationale
templates, an empty critic checklist, and an empty fixture list.
Task directories go directly under the root (same layout as
``examples/evals/structured-output/tasks/``). The self-test gate
refuses an untouched scaffold (empty fixtures), so an unfinished
benchmark can never launch: authoring means filling the templates,
adding tasks, and turning the gate green.
"""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.errors import SpecError
from roast_my_harness.evals.selftest import VALIDATION_DIRNAME, selftest_path

EVAL_TOML_TEMPLATE = """\
# Eval contract for {eval_id}. Freeze before any control/variant run:
# any edit changes the eval hash and starts a new run.
schema_version = 1
id = "{eval_id}"
title = "{title}"

[scoring]
pass_threshold = {threshold}

# Uncomment to pin a model judge. A judge score without this contract
# fails the self-test gate.
# [judge]
# enabled = true
# model = "..."
# rubric = "v1"
# samples = 3
"""

CAPABILITY_MAP_TEMPLATE = """\
{
  "target": "TODO: path or description of the thing being evaluated",
  "source": "TODO: how this map was produced (model, date)",
  "capabilities": []
}
"""

RATIONALE_TEMPLATE = """\
# Rationale: {eval_id}

## Target

TODO: what is being evaluated and what problem it claims to solve.

## Workflow (do not skip steps)

1. Map capabilities from the target into `capability-map.json`.
2. Design 4-8 tasks from the map ALONE (never from skill source).
3. Write a deterministic-first verifier per task; freeze the contract.
4. Add good/bad fixtures per task to `validation/self-test.json`.
5. Critic pass over `validation/critic.json`; one repair round.
6. Control-only calibration run; fix broken mechanics only.
7. Freeze: record revision below. Immutable from here.

## Freeze

TODO: revision + date once validation is green.
"""

CRITIC_TEMPLATE = """\
{
  "verdict": "todo",
  "repairs": 0,
  "checks": []
}
"""

SELFTEST_TEMPLATE = """\
{
  "fixtures": []
}
"""

TASKS_README = """\
# Tasks for this eval

Add one directory per task directly under this root, each with
`task.toml`, `instruction.md`, `environment/`, and `tests/`. See
`examples/evals/structured-output/tasks/` for a worked 3-task eval
with host-testable deterministic verifiers. Discovery fails until at
least one task exists; the self-test gate fails until
`validation/self-test.json` discriminates.
"""


def init_eval(
    root: Path,
    *,
    eval_id: str,
    title: str = "",
    threshold: float = 0.7,
) -> Path:
    """Write a custom-eval scaffold; raises SpecError when started already."""
    root = Path(root).expanduser()
    descriptor_path = root / "eval.toml"
    if descriptor_path.exists():
        raise SpecError(f"eval workspace already started: {descriptor_path}")
    if not 0.0 < threshold <= 1.0:
        raise SpecError("pass_threshold must satisfy 0 < t <= 1")
    validation = root / VALIDATION_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    validation.mkdir(parents=True, exist_ok=True)
    descriptor_path.write_text(
        EVAL_TOML_TEMPLATE.format(eval_id=eval_id, title=title or eval_id, threshold=threshold),
        encoding="utf-8",
    )
    (root / "capability-map.json").write_text(CAPABILITY_MAP_TEMPLATE, encoding="utf-8")
    (root / "rationale.md").write_text(RATIONALE_TEMPLATE.format(eval_id=eval_id), encoding="utf-8")
    (validation / "critic.json").write_text(CRITIC_TEMPLATE, encoding="utf-8")
    selftest_path(root).write_text(SELFTEST_TEMPLATE, encoding="utf-8")
    (root / "TASKS-README.md").write_text(TASKS_README, encoding="utf-8")
    # Templates must parse so authors iterate against real errors, not
    # syntax noise; content checks stay the gate's job.
    json.loads((root / "capability-map.json").read_text(encoding="utf-8"))
    json.loads((validation / "critic.json").read_text(encoding="utf-8"))
    json.loads(selftest_path(root).read_text(encoding="utf-8"))
    return root


__all__ = ["init_eval"]
