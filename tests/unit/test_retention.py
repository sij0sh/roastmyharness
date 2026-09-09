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


def test_data_dir_defaults_to_dotdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roast_my_harness import paths as paths_mod

    monkeypatch.delenv("ROAST_MY_HARNESS_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths_mod.data_dir() == tmp_path / ".roastmyharness"
    monkeypatch.setenv("ROAST_MY_HARNESS_DATA_DIR", str(tmp_path / "custom"))
    assert paths_mod.data_dir() == tmp_path / "custom"
