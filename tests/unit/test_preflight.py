"""Preflight checks that do not require live infrastructure."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from roast_my_harness.runner import preflight


def _spec(*, extensions=(), setup=()):
    variant = SimpleNamespace(extensions=list(extensions), setup=list(setup))
    return SimpleNamespace(arms=lambda: [variant])


def test_npm_packages_rejects_unavailable_pin(monkeypatch):
    spec = _spec(
        extensions=[SimpleNamespace(kind="npm", package="missing@1.0.0")]
    )
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/npm")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1, "", "E404")

    monkeypatch.setattr(preflight.subprocess, "run", run)

    results = preflight._npm_packages(spec)

    assert len(results) == 1
    assert results[0].status == "fail"
    assert results[0].name == "npm package missing@1.0.0"
    assert "not available" in results[0].detail
    assert calls[0][0] == [
        "/usr/bin/npm", "view", "missing@1.0.0", "version", "--json"
    ]
    assert calls[0][1]["timeout"] == 60


def test_npm_packages_checks_extensions_and_setup_once(monkeypatch):
    package = "available@1.2.3"
    spec = _spec(
        extensions=[SimpleNamespace(kind="npm", package=package)],
        setup=[SimpleNamespace(handler="npm_pi_install", package=package)],
    )
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/npm")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '"1.2.3"', "")

    monkeypatch.setattr(preflight.subprocess, "run", run)

    results = preflight._npm_packages(spec)

    assert [(result.status, result.detail) for result in results] == [
        ("pass", "available")
    ]
    assert len(calls) == 1


def test_npm_packages_requires_host_npm(monkeypatch):
    spec = _spec(
        extensions=[SimpleNamespace(kind="npm", package="package@1.0.0")]
    )
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    results = preflight._npm_packages(spec)

    assert results[0].status == "fail"
    assert results[0].name == "npm"
