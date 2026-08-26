"""Pier argv construction (argument arrays, never shell strings)."""

from __future__ import annotations

from pathlib import Path

from roast_my_harness.runner import pier


def test_version_satisfies():
    assert pier.version_satisfies("0.3.1", ">=0.3,<0.4")
    assert not pier.version_satisfies("0.4.0", ">=0.3,<0.4")
    assert not pier.version_satisfies("0.2.9", ">=0.3,<0.4")
    assert pier.version_satisfies("0.3.0", ">=0.3,<0.4")


def test_build_run_args(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pier, "pier_executable", lambda: "pier")
    args = pier.build_run_args(
        task_root=tmp_path / "tasks",
        jobs_dir=tmp_path / "jobs" / "a",
        job_name="exp-a",
        manifest_path=tmp_path / "m.json",
        model_id="openai-codex/gpt-5.6-luna",
        thinking="high",
        pi_version="0.84.3",
        n_concurrent=2,
        include_tasks=["t1", "t2"],
        agent_env={"FOO": "BAR"},
    )
    assert args[0] == "pier"
    assert args[1] == "run"
    assert "--include-task-name" in args
    assert args.count("--include-task-name") == 2
    assert "variant_manifest=" + str(tmp_path / "m.json") in args
    assert "thinking=high" in args
    assert "pi_version=0.84.3" in args
    assert "openai-codex/gpt-5.6-luna" in args
    assert "--yes" in args
    assert "FOO=BAR" in args
    joined = " ".join(args)
    assert "|" not in joined and ";" not in joined and "&&" not in joined
