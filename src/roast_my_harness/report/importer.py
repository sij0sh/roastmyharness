"""Import legacy DSE-tests results into the control-observation pool.

Walks results-* trees (layout: <variant>/<job-timestamp>/<trial>/) and
records bare-control observations. Imported rows carry unknown cohort
fields (no pi_version/adapter protocol in legacy artifacts), so they are
recorded with eligible=0: visible for reference, never auto-reused
(plan sections 16 and 22).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from roast_my_harness.store.repository import Repository


@dataclass
class ImportReport:
    scanned: int = 0
    imported: int = 0
    skipped_error: int = 0
    skipped_variant: int = 0
    details: list[str] = field(default_factory=list)


PASS_THRESHOLD = 0.999
CONTROL_VARIANT_NAMES = {"bare"}


def import_dse_results(
    repo: Repository, results_root: Path, *, variants: list[str] | None = None
) -> ImportReport:
    """Record legacy control observations as ineligible reference rows."""
    report = ImportReport()
    wanted = set(variants) if variants else CONTROL_VARIANT_NAMES
    if not results_root.is_dir():
        report.details.append(f"missing results root: {results_root}")
        return report
    for variant_dir in sorted(results_root.iterdir()):
        if not variant_dir.is_dir() or variant_dir.name not in wanted:
            continue
        for result_path in sorted(variant_dir.rglob("result.json")):
            trial_dir = result_path.parent
            if trial_dir.name == variant_dir.name and result_path.parent.parent == results_root:
                continue  
            if not ((trial_dir / "agent").is_dir() or (trial_dir / "verifier").is_dir()):
                report.skipped_variant += 1
                continue
            report.scanned += 1
            try:
                data = json.loads(result_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                report.skipped_error += 1
                report.details.append(f"{trial_dir}: unreadable ({e})")
                continue
            outcome = _terminal_outcome(data)
            if outcome is None:
                report.skipped_error += 1
                continue
            resolved, reward, finished_at = outcome
            task_checksum = str(data.get("task_checksum") or "")
            cohort_key = f"legacy-dse:{variant_dir.name}:{task_checksum}"
            observed = finished_at or datetime.now(UTC).isoformat()
            repo.record_control_observation(
                cohort_key=cohort_key,
                task_hash=f"legacy-dse:{task_checksum}",
                trial_id=str(data.get("trial_uri") or trial_dir),
                resolved=resolved,
                reward=reward,
                observed_at=observed,
                eligible=False,
                source=f"dse-import:{variant_dir.name}",
            )
            report.imported += 1
    return report


def _terminal_outcome(data: dict) -> tuple[bool, float, str] | None:
    """Pass/fail/reward from a legacy result.json, or None for errors."""
    if data.get("exception_info"):
        return None
    verifier = data.get("verifier_result") or {}
    rewards = verifier.get("rewards") or {}
    reward = rewards.get("reward")
    if reward is None:
        return None
    return float(reward) >= PASS_THRESHOLD, float(reward), str(
        data.get("finished_at") or ""
    )
