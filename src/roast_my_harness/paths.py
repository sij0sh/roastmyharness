"""Filesystem policy. Single home dir by default, env overrides documented."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "roastmyharness"
APP_DIR_NAME = ".roastmyharness"
DATA_DIR_ENV = "ROAST_MY_HARNESS_DATA_DIR"
RUNS_DIR_ENV = "ROAST_MY_HARNESS_RUNS_DIR"
CACHE_DIR_ENV = "ROAST_MY_HARNESS_CACHE_DIR"


def data_dir() -> Path:
    """Home for database, runs, plans, and config.

    Precedence: ROAST_MY_HARNESS_DATA_DIR, else ~/.roastmyharness on
    every platform (Path.home() resolves the Windows profile too).
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / APP_DIR_NAME


def legacy_data_dir() -> Path:
    """Pre-0.2 platformdirs location, kept for migration notes only."""
    from platformdirs import user_data_dir

    return Path(user_data_dir(APP_NAME))


def legacy_cache_root() -> Path:
    """Pre-0.2 platformdirs cache location, kept for migration notes only."""
    from platformdirs import user_cache_dir

    return Path(user_cache_dir(APP_NAME))


def database_path() -> Path:
    return data_dir() / "roastmyharness.db"


def plans_dir() -> Path:
    return data_dir() / "plans"


def config_path() -> Path:
    return data_dir() / "config.toml"


def cache_root() -> Path:
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return data_dir() / "cache"


def homes_cache_dir() -> Path:
    return cache_root() / "homes"


def runs_root() -> Path:
    override = os.environ.get(RUNS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return data_dir() / "runs"


def run_dir(experiment_id: str) -> Path:
    return runs_root() / experiment_id
