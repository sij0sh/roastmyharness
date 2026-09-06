"""Version pins: latest sentinel validation and npm resolution."""

from __future__ import annotations

import subprocess

import pytest

from roast_my_harness.adapter import versions


def test_validate_agent_pin_accepts_latest_and_exact():
    assert versions.validate_agent_pin("latest", "pi_version") == "latest"
    assert versions.validate_agent_pin("0.85.1", "pi_version") == "0.85.1"
    with pytest.raises(ValueError, match="pi_version"):
        versions.validate_agent_pin("0.85.1; echo leaked", "pi_version")
    with pytest.raises(ValueError, match="pi_version"):
        versions.validate_agent_pin("^0.85.1", "pi_version")


def test_validate_exact_pin_rejects_latest():
    assert versions.validate_exact_pin("18.0.9", "agent_version") == "18.0.9"
    with pytest.raises(ValueError, match="agent_version"):
        versions.validate_exact_pin("latest", "agent_version")


def test_resolve_exact_pin_needs_no_network(monkeypatch):
    monkeypatch.setattr(versions.shutil, "which", lambda name: None)
    assert versions.resolve_package_version("pkg", "0.85.1") == "0.85.1"


def test_resolve_latest_queries_npm(monkeypatch):
    versions.resolve_package_version.cache_clear()
    monkeypatch.setattr(versions.shutil, "which", lambda name: "/usr/bin/npm")

    def run(argv, **kwargs):
        assert argv == ["/usr/bin/npm", "view", "some-pkg", "version", "--json"]
        return subprocess.CompletedProcess(argv, 0, '"0.85.1"', "")

    monkeypatch.setattr(versions.subprocess, "run", run)
    try:
        assert versions.resolve_package_version("some-pkg", "latest") == "0.85.1"
    finally:
        versions.resolve_package_version.cache_clear()


def test_resolve_latest_fails_without_npm(monkeypatch):
    versions.resolve_package_version.cache_clear()
    monkeypatch.setattr(versions.shutil, "which", lambda name: None)
    try:
        with pytest.raises(RuntimeError, match="npm not on PATH"):
            versions.resolve_package_version("some-pkg", "latest")
    finally:
        versions.resolve_package_version.cache_clear()
