"""Fixture self-tests: the unit-test suite for a custom benchmark.

Every generated eval ships ``validation/self-test.json``: synthetic
verifier outputs (rewards maps) with expected resolved outcomes. The
gate evaluates them against the frozen scoring contract instead of
executing verifiers, so it runs in milliseconds inside preflight:

- the scalar ``reward`` is required and decides resolved/not-resolved
  at the contract's pass_threshold, mirroring reconcile;
- undeclared judges are refused (a ``reward_judge`` with the judge
  disabled, or a judge score without ``judge_model``), so no eval
  quietly grades on an unpinned model;
- the fixture set must discriminate (at least one expected pass and
  one expected failure), or the contract is vacuous.

This locks the scoring semantics and catches vacuous contracts. It
does not prove verifier implementations correct — that stays the job
of the critic checklist and control-only calibration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roast_my_harness.errors import SpecError
from roast_my_harness.evals.descriptor import EvalDescriptor
from roast_my_harness.tasks.discover import is_task_dir
from roast_my_harness.telemetry.result import (
    DETERMINISTIC_KEY,
    JUDGE_KEY,
    JUDGE_MODEL_KEY,
    fnum_or_none,
)

SELFTEST_FILENAME = "self-test.json"
VALIDATION_DIRNAME = "validation"


@dataclass(frozen=True)
class SelfTestFailure:
    fixture: str
    message: str


@dataclass(frozen=True)
class SelfTestResult:
    passed: bool
    evaluated: int
    failures: tuple[SelfTestFailure, ...] = ()


def selftest_path(task_root: Path) -> Path:
    """Location of the fixture file beside a task root."""
    return Path(task_root) / VALIDATION_DIRNAME / SELFTEST_FILENAME


def load_fixtures(task_root: Path) -> list[dict[str, Any]]:
    """Parse the fixture file; raises SpecError on any defect."""
    path = selftest_path(task_root)
    if not path.is_file():
        raise SpecError(f"eval self-test {path} not found")
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise SpecError(f"cannot read eval self-test {path}: {e}") from e
    if not isinstance(raw, dict) or not isinstance(raw.get("fixtures"), list):
        raise SpecError(f"invalid eval self-test {path}: needs a fixtures list")
    fixtures = raw["fixtures"]
    if not fixtures:
        raise SpecError(f"invalid eval self-test {path}: fixtures list is empty")
    for entry in fixtures:
        if not isinstance(entry, dict):
            raise SpecError(f"invalid eval self-test {path}: fixture must be a mapping")
    return fixtures


def evaluate_fixtures(
    descriptor: EvalDescriptor,
    fixtures: list[dict[str, Any]],
    task_root: Path,
) -> SelfTestResult:
    """Check fixtures against the frozen scoring contract."""
    failures: list[SelfTestFailure] = []
    seen_pass = False
    seen_fail = False
    for index, fixture in enumerate(fixtures):
        name = str(fixture.get("name") or f"fixture-{index}")

        def fail(message: str, _fixture: str = name) -> None:
            failures.append(SelfTestFailure(fixture=_fixture, message=message))

        task = fixture.get("task")
        if not task or not isinstance(task, str):
            fail("task is required and must be a string")
            continue
        candidates = (task_root / "tasks" / task, task_root / task)
        if not any(is_task_dir(path) for path in candidates):
            fail(f"task {task!r} is not a task directory under the eval root")
            continue
        rewards = fixture.get("rewards")
        if not isinstance(rewards, dict):
            fail("rewards is required and must be a mapping")
            continue
        reward = fnum_or_none(rewards.get("reward"))
        if reward is None:
            fail("rewards.reward is required: the scalar reward stays primary")
            continue
        expected = fixture.get("expect_resolved")
        if not isinstance(expected, bool):
            fail("expect_resolved is required and must be true/false")
            continue
        seen_pass |= expected
        seen_fail |= not expected
        predicted = reward >= descriptor.pass_threshold
        if predicted != expected:
            fail(
                f"reward {reward} resolves to {predicted} at pass_threshold "
                f"{descriptor.pass_threshold}, expected {expected}"
            )
        judge_score = fnum_or_none(rewards.get(JUDGE_KEY))
        judge_model = rewards.get(JUDGE_MODEL_KEY)
        if judge_score is not None or judge_model:
            if not descriptor.judge_enabled:
                fail(
                    f"{JUDGE_KEY} data needs [judge] enabled in the eval contract; "
                    "no eval quietly grades on an unpinned model"
                )
            elif judge_score is not None and not judge_model:
                fail(f"{JUDGE_KEY} score needs {JUDGE_MODEL_KEY} for provenance")
        for key in (DETERMINISTIC_KEY,):
            if key in rewards and fnum_or_none(rewards.get(key)) is None:
                fail(f"{key} must be numeric when present")
    if not seen_pass or not seen_fail:
        failures.append(
            SelfTestFailure(
                fixture="(set)",
                message=(
                    "fixture set must discriminate: needs at least one "
                    "expect_resolved = true and one false"
                ),
            )
        )
    return SelfTestResult(
        passed=not failures, evaluated=len(fixtures), failures=tuple(failures)
    )


def run_selftests(
    task_root: Path, descriptor: EvalDescriptor
) -> SelfTestResult:
    """Load and evaluate an eval root's fixtures; raises SpecError."""
    return evaluate_fixtures(descriptor, load_fixtures(task_root), task_root)
