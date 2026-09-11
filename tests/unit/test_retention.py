"""Retention: size parsing, settings precedence, oldest-first pruning."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from roast_my_harness.store import retention as retention_mod


def _sized_dir(path: Path, size: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "data.bin").write_bytes(b"x" * size)


def test_parse_size_suffixes() -> None:
    assert retention_mod.parse_size("500mb") == 500 * 1024**2
    assert retention_mod.parse_size("1GB") == 1024**3
    assert retention_mod.parse_size("1.5 GB") == int(1.5 * 1024**3)
    assert retention_mod.parse_size(1024) == 1024
    with pytest.raises(ValueError):
        retention_mod.parse_size("ten mb")


def test_parse_enabled_toggles() -> None:
    assert retention_mod.parse_enabled("false") is False
    assert retention_mod.parse_enabled("0") is False
    assert retention_mod.parse_enabled("off") is False
    assert retention_mod.parse_enabled("true") is True
    with pytest.raises(ValueError):
        retention_mod.parse_enabled("maybe")


def test_enforce_deletes_oldest_first(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    old = runs / "exp-old"
    mid = runs / "exp-mid"
    new = runs / "exp-new"
    _sized_dir(old, 400)
    _sized_dir(mid, 400)
    _sized_dir(new, 400)
    now = time.time()
    os.utime(old, (now - 300, now - 300))
    os.utime(mid, (now - 200, now - 200))
    os.utime(new, (now - 100, now - 100))
    result = retention_mod.enforce_retention(runs, 700, exclude={"exp-new"})
    assert result is not None
    assert not old.exists()
    assert not mid.exists()
    assert new.exists()
    assert result.total_after <= 700


def test_enforce_never_deletes_excluded_active(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _sized_dir(runs / "active", 1000)
    result = retention_mod.enforce_retention(runs, 10, exclude={"active"})
    assert result is not None
    assert (runs / "active").exists()
    assert result.skipped_active == "active"
    assert result.deleted == []


def test_enforce_prunes_db_rows(tmp_path: Path) -> None:
    from roast_my_harness.store.repository import Repository

    runs = tmp_path / "runs"
    _sized_dir(runs / "exp-a", 600)
    _sized_dir(runs / "exp-b", 600)
    db = tmp_path / "test.db"
    repo = Repository(db)
    repo.create_experiment(
        experiment_id="exp-a",
        name="a",
        spec={},
        spec_hash="h",
        run_dir=str(runs / "exp-a"),
    )
    repo.create_experiment(
        experiment_id="exp-b",
        name="b",
        spec={},
        spec_hash="h",
        run_dir=str(runs / "exp-b"),
    )
    repo.close()
    now = time.time()
    os.utime(runs / "exp-a", (now - 200, now - 200))
    os.utime(runs / "exp-b", (now - 100, now - 100))
    result = retention_mod.enforce_retention(runs, 700, db_path=db)
    assert result is not None
    assert result.deleted == ["exp-a"]
    repo = Repository(db)
    try:
        assert repo.get_experiment("exp-a") is None
        assert repo.get_experiment("exp-b") is not None
    finally:
        repo.close()


def test_storage_policy_disabled_by_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    _sized_dir(runs / "exp-a", 1000)
    monkeypatch.setenv("ROAST_MY_HARNESS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ROAST_MY_HARNESS_RUNS_DIR", str(runs))
    monkeypatch.setenv("ROAST_MY_HARNESS_RETENTION", "false")
    settings = retention_mod.load_storage_settings()
    assert settings.retention_enabled is False
    assert (
        retention_mod.enforce_storage_policy(progress=lambda _m: None) is None
    )
    assert (runs / "exp-a").exists()


def test_storage_settings_from_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROAST_MY_HARNESS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ROAST_MY_HARNESS_RUNS_DIR", raising=False)
    monkeypatch.delenv("ROAST_MY_HARNESS_RETENTION", raising=False)
    monkeypatch.delenv("ROAST_MY_HARNESS_RETENTION_MAX_SIZE", raising=False)
    monkeypatch.delenv("ROAST_MY_HARNESS_RETENTION_MAX_BYTES", raising=False)
    (tmp_path / "config.toml").write_text(
        '[retention]\nenabled = true\nmax_size = "1MB"\n'
    )
    settings = retention_mod.load_storage_settings()
    assert settings.retention_enabled is True
    assert settings.retention_max_bytes == 1024**2


def _clean_env(monkeypatch: pytest.MonkeyPatch, data: Path) -> None:
    monkeypatch.setenv("ROAST_MY_HARNESS_DATA_DIR", str(data))
    for key in (
        "ROAST_MY_HARNESS_RUNS_DIR",
        "ROAST_MY_HARNESS_RETENTION",
        "ROAST_MY_HARNESS_RETENTION_MAX_SIZE",
        "ROAST_MY_HARNESS_RETENTION_MAX_BYTES",
        "ROAST_MY_HARNESS_RETENTION_VARIANT_MAX_SIZE",
        "ROAST_MY_HARNESS_RETENTION_VARIANT_MAX_BYTES",
        "ROAST_MY_HARNESS_RETENTION_CONTROL_MAX_SIZE",
        "ROAST_MY_HARNESS_RETENTION_CONTROL_MAX_BYTES",
        "ROAST_MY_HARNESS_RETENTION_CONTROL_KEEP",
        "ROAST_MY_HARNESS_RETENTION_CONTROL_RETENTION",
    ):
        monkeypatch.delenv(key, raising=False)


def test_split_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch, tmp_path)
    settings = retention_mod.load_storage_settings()
    assert settings.variant_max_bytes == 3 * 1024**3
    assert settings.control_max_bytes == 5 * 1024**3
    assert settings.control_keep == 4
    assert settings.control_retention is False


def test_split_config_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch, tmp_path)
    (tmp_path / "config.toml").write_text(
        "[retention]\n"
        "enabled = true\n"
        'variant_max_size = "1MB"\n'
        'control_max_size = "2MB"\n'
        "control_keep = 2\n"
        "control_retention = true\n"
    )
    settings = retention_mod.load_storage_settings()
    assert settings.variant_max_bytes == 1024**2
    assert settings.control_max_bytes == 2 * 1024**2
    assert settings.control_keep == 2
    assert settings.control_retention is True
    monkeypatch.setenv("ROAST_MY_HARNESS_RETENTION_CONTROL_KEEP", "6")
    assert retention_mod.load_storage_settings().control_keep == 6


def test_legacy_max_size_feeds_variant_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch, tmp_path)
    (tmp_path / "config.toml").write_text(
        '[retention]\nenabled = true\nmax_size = "1MB"\n'
    )
    settings = retention_mod.load_storage_settings()
    assert settings.variant_max_bytes == 1024**2
    assert settings.control_max_bytes == 5 * 1024**3


def _control_trial(
    runs: Path, exp: str, task: str, stamp: str, size: int = 100
) -> Path:
    trial = runs / exp / "jobs" / "control" / f"{task}__X"
    trial.mkdir(parents=True, exist_ok=True)
    (trial / "data.bin").write_bytes(b"x" * size)
    return trial


def _seed_control_db(db: Path, runs: Path, exps: list[str]) -> None:
    from roast_my_harness.store.repository import Repository

    repo = Repository(db)
    try:
        for i, exp in enumerate(exps):
            repo.create_experiment(
                experiment_id=exp,
                name=exp,
                spec={"model": "openai-codex/gpt-5.6-luna", "thinking": "high"},
                spec_hash="h",
                run_dir=str(runs / exp),
            )
            trial = _control_trial(runs, exp, "t1", f"2026-01-0{i + 1}T00:00:00")
            repo.upsert_trial(
                experiment_id=exp,
                variant_id="control",
                task_id="t1",
                attempt=1,
                status="pass",
                job_path=str(trial),
                reward=1.0,
                resolved=True,
                exception_type=None,
                metrics=None,
                finished_at=f"2026-01-0{i + 1}T00:00:00",
            )
    finally:
        repo.close()


def test_control_group_prune_keeps_newest(tmp_path: Path) -> None:
    from roast_my_harness.store.repository import Repository

    runs = tmp_path / "runs"
    db = tmp_path / "test.db"
    Repository(db).close()
    _seed_control_db(db, runs, ["exp-a", "exp-b", "exp-c"])
    pruned, _freed = retention_mod.prune_control_groups(runs, db, 2)
    assert len(pruned) == 1
    assert not (runs / "exp-a" / "jobs" / "control" / "t1__X").exists()
    assert (runs / "exp-b" / "jobs" / "control" / "t1__X").exists()
    assert (runs / "exp-c" / "jobs" / "control" / "t1__X").exists()
    repo = Repository(db)
    try:
        rows = repo.conn.execute(
            "SELECT experiment_id FROM trials WHERE variant_id='control'"
        ).fetchall()
        assert sorted(r["experiment_id"] for r in rows) == ["exp-b", "exp-c"]
    finally:
        repo.close()


def test_control_retention_skips_size_cap(tmp_path: Path) -> None:
    from roast_my_harness.store import retention as mod

    runs = tmp_path / "runs"
    _sized_dir(runs / "exp-a" / "jobs" / "control", 1000)
    _sized_dir(runs / "exp-b" / "jobs" / "control", 1000)
    now = time.time()
    os.utime(runs / "exp-a", (now - 200, now - 200))
    os.utime(runs / "exp-b", (now - 100, now - 100))
    capped = mod.enforce_split_retention(
        runs,
        variant_max_bytes=10**9,
        control_max_bytes=1500,
        enforce_control_cap=True,
        db_path=None,
    )
    assert capped.deleted == ["exp-a"]
    _sized_dir(runs / "exp-a" / "jobs" / "control", 1000)
    kept = mod.enforce_split_retention(
        runs,
        variant_max_bytes=10**9,
        control_max_bytes=1500,
        enforce_control_cap=False,
        db_path=None,
    )
    assert kept.deleted == []
    assert (runs / "exp-a" / "jobs" / "control").exists()


def test_policy_master_switch_keeps_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    _sized_dir(runs / "exp-a", 1000)
    monkeypatch.setenv("ROAST_MY_HARNESS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ROAST_MY_HARNESS_RUNS_DIR", str(runs))
    monkeypatch.setenv("ROAST_MY_HARNESS_RETENTION", "false")
    assert retention_mod.enforce_storage_policy() is None
    assert (runs / "exp-a").exists()


def test_data_dir_defaults_to_dotdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roast_my_harness import paths as paths_mod

    monkeypatch.delenv("ROAST_MY_HARNESS_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths_mod.data_dir() == tmp_path / ".roastmyharness"
    monkeypatch.setenv("ROAST_MY_HARNESS_DATA_DIR", str(tmp_path / "custom"))
    assert paths_mod.data_dir() == tmp_path / "custom"
