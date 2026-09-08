"""Empty-patch and artifact-failure classification over synthetic trial dirs."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.runner.patch_guard import (
    INFRA_ARTIFACT_COPY,
    INVALID_EMPTY_PATCH,
    agent_mutation_evidence,
    classify_empty_patch,
    manifest_model_patch_status,
    patch_size_bytes,
    worktree_dirty,
)


def make_trial_dir(root: Path, name: str = "t1__X") -> Path:
    trial = root / name
    (trial / "agent").mkdir(parents=True, exist_ok=True)
    (trial / "verifier").mkdir(parents=True, exist_ok=True)
    (trial / "artifacts").mkdir(parents=True, exist_ok=True)
    return trial


def write_manifest(trial: Path, status: str) -> None:
    (trial / "artifacts" / "manifest.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "source": "/logs/artifacts/model.patch",
                        "destination": "artifacts/model.patch",
                        "type": "file",
                        "status": status,
                    }
                ]
            }
        )
    )


def test_missing_patch_without_evidence_is_legit_zero(tmp_path: Path):
    trial = make_trial_dir(tmp_path)
    assert patch_size_bytes(trial) is None
    assert manifest_model_patch_status(trial) is None
    assert worktree_dirty(trial) is None
    assert not agent_mutation_evidence(trial)
    assert classify_empty_patch(trial) is None


def test_nonempty_patch_never_flagged(tmp_path: Path):
    trial = make_trial_dir(tmp_path)
    (trial / "artifacts" / "model.patch").write_text("diff --git a/f b/f\n")
    (trial / "artifacts" / "worktree-status.txt").write_text(" M f\n")
    write_manifest(trial, "ok")
    assert classify_empty_patch(trial) is None


def test_failed_artifact_copy_is_infra(tmp_path: Path):
    trial = make_trial_dir(tmp_path)
    write_manifest(trial, "failed")
    assert manifest_model_patch_status(trial) == "failed"
    assert classify_empty_patch(trial) == INFRA_ARTIFACT_COPY


def test_dirty_worktree_with_empty_patch_is_invalid(tmp_path: Path):
    trial = make_trial_dir(tmp_path)
    (trial / "artifacts" / "model.patch").write_text("")
    (trial / "artifacts" / "worktree-status.txt").write_text(" M src/app.py\n?? new_file.py\n")
    assert worktree_dirty(trial) is True
    assert classify_empty_patch(trial) == INVALID_EMPTY_PATCH


def test_clean_worktree_with_empty_patch_is_legit_zero(tmp_path: Path):
    trial = make_trial_dir(tmp_path)
    (trial / "artifacts" / "model.patch").write_text("")
    (trial / "artifacts" / "worktree-status.txt").write_text("")
    write_manifest(trial, "ok")
    assert worktree_dirty(trial) is False
    assert classify_empty_patch(trial) is None


def test_trajectory_write_call_is_mutation_evidence(tmp_path: Path):
    trial = make_trial_dir(tmp_path)
    (trial / "agent" / "trajectory.json").write_text(
        json.dumps(
            {
                "steps": [
                    {"step_id": 1, "tool_calls": [{"function_name": "read"}]},
                    {"step_id": 2, "tool_calls": [{"function_name": "write"}]},
                ]
            }
        )
    )
    assert agent_mutation_evidence(trial) is True
    # Legacy run: no status file, empty patch, but the agent wrote files.
    assert classify_empty_patch(trial) == INVALID_EMPTY_PATCH


def test_read_and_bash_only_is_not_mutation_evidence(tmp_path: Path):
    trial = make_trial_dir(tmp_path)
    (trial / "agent" / "trajectory.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "step_id": 1,
                        "tool_calls": [
                            {"function_name": "read"},
                            {"function_name": "bash"},
                        ],
                    }
                ]
            }
        )
    )
    (trial / "agent" / "pi-events.jsonl").write_text(
        '{"type": "tool_execution_start", "toolName": "read", "args": {}}\n'
    )
    assert agent_mutation_evidence(trial) is False
    assert classify_empty_patch(trial) is None


def test_events_write_call_is_mutation_evidence(tmp_path: Path):
    trial = make_trial_dir(tmp_path)
    (trial / "agent" / "pi-events.jsonl").write_text(
        '{"type": "turn_end", "message": {}}\n'
        '{"type": "tool_execution_start", "toolName": "edit", "args": {}}\n'
    )
    assert agent_mutation_evidence(trial) is True


def test_corrupt_files_are_unknown_not_failure(tmp_path: Path):
    trial = make_trial_dir(tmp_path)
    (trial / "artifacts" / "manifest.json").write_text("{not json")
    (trial / "agent" / "trajectory.json").write_text("[broken")
    (trial / "agent" / "pi-events.jsonl").write_text("\x00\x01binary")
    assert manifest_model_patch_status(trial) is None
    assert not agent_mutation_evidence(trial)
    assert classify_empty_patch(trial) is None


def make_stepped_trial(root: Path, name: str = "t2__X") -> Path:
    """Trial dir in post-relocation staged layout (no trial-root logs)."""
    trial = root / name
    for step in ("a", "b"):
        for sub in ("agent", "verifier", "artifacts"):
            (trial / "steps" / step / sub).mkdir(parents=True, exist_ok=True)
    return trial


def test_step_dirs_sorted_and_detected():
    import tempfile

    from roast_my_harness.runner.patch_guard import (
        has_trial_logs,
        is_stepped_trial,
        step_dirs,
    )

    with tempfile.TemporaryDirectory() as tmp:
        trial = make_stepped_trial(Path(tmp))
        assert [p.name for p in step_dirs(trial)] == ["a", "b"]
        assert is_stepped_trial(trial)
        assert has_trial_logs(trial)


def test_has_trial_logs_rejects_job_dirs():
    import tempfile

    from roast_my_harness.runner.patch_guard import has_trial_logs

    with tempfile.TemporaryDirectory() as tmp:
        assert not has_trial_logs(Path(tmp))
        flat = make_trial_dir(Path(tmp), "t1__X")
        assert has_trial_logs(flat)


def test_patch_size_falls_back_to_last_step():
    import tempfile

    from roast_my_harness.runner.patch_guard import patch_size_bytes

    with tempfile.TemporaryDirectory() as tmp:
        trial = make_stepped_trial(Path(tmp))
        (trial / "steps" / "a" / "artifacts" / "model.patch").write_text("xxx")
        (trial / "steps" / "b" / "artifacts" / "model.patch").write_text("xxxxx")
        assert patch_size_bytes(trial) == 5


def test_mutation_evidence_scans_step_logs():
    import tempfile

    from roast_my_harness.runner.patch_guard import agent_mutation_evidence

    with tempfile.TemporaryDirectory() as tmp:
        trial = make_stepped_trial(Path(tmp))
        assert not agent_mutation_evidence(trial)
        (trial / "steps" / "b" / "agent" / "pi-events.jsonl").write_text(
            '{"type": "tool_execution_start", "toolName": "edit"}\n'
        )
        assert agent_mutation_evidence(trial)
