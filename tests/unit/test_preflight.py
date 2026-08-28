"""Preflight checks that do not require live infrastructure."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from roast_my_harness.runner import preflight


def _spec(
    *,
    extensions=(),
    setup=(),
    agent_id="pi",
    agent_version="0.84.3",
):
    variant = SimpleNamespace(extensions=list(extensions), setup=list(setup))
    agents = {"control": agent_id}
    return SimpleNamespace(
        arms=lambda: [variant],
        resolved_agents=lambda: agents,
        agent_version_for=lambda _agent_id: agent_version,
    )


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

    assert len(results) == 2
    by_name = {result.name: result for result in results}
    failed = by_name["npm package missing@1.0.0"]
    assert failed.status == "fail"
    assert "not available" in failed.detail
    missing_call = next(call for call in calls if call[0][2] == "missing@1.0.0")
    assert missing_call[0] == [
        "/usr/bin/npm", "view", "missing@1.0.0", "version", "--json"
    ]
    assert missing_call[1]["timeout"] == 60
    probed = {call[0][2] for call in calls}
    assert "@earendil-works/pi-coding-agent@0.84.3" in probed


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

    assert all(result.status == "pass" for result in results)
    assert len(results) == 2
    probed = [call[2] for call in calls]
    assert sorted(probed).count("available@1.2.3") == 1
    assert "@earendil-works/pi-coding-agent@0.84.3" in probed


def test_npm_packages_requires_host_npm(monkeypatch):
    spec = _spec(
        extensions=[SimpleNamespace(kind="npm", package="package@1.0.0")]
    )
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    results = preflight._npm_packages(spec)

    assert results[0].status == "fail"
    assert results[0].name == "npm"
