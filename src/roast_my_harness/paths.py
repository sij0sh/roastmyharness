"""Filesystem policy. XDG paths by default, env overrides where documented."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir

APP_NAME = "roast-my-harness"
RUNS_DIR_ENV = "ROAST_MY_HARNESS_RUNS_DIR"


def config_dir() -> Path:
    return Path(user_config_dir(APP_NAME))


def config_file() -> Path:
    return config_dir() / "config.toml"


def data_dir() -> Path:
    return Path(user_data_dir(APP_NAME))


def database_path() -> Path:
    return data_dir() / "roast-my-harness.db"


def cache_root() -> Path:
    return Path(user_cache_dir(APP_NAME))


def homes_cache_dir() -> Path:
    return cache_root() / "homes"


def runs_root() -> Path:
    override = os.environ.get(RUNS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return data_dir() / "runs"


def run_dir(experiment_id: str) -> Path:
    return runs_root() / experiment_id
