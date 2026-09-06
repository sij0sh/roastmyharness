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
        resolved_version_for=lambda _agent_id: agent_version,
    )


def test_npm_packages_trusts_exact_pins_without_registry(monkeypatch):
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
    assert all(r.status == "pass" for r in results)
    by_name = {result.name: result for result in results}
    assert "trusted without registry" in by_name["npm package missing@1.0.0"].detail
    assert calls == []


def test_npm_packages_rejects_unavailable_latest(monkeypatch):
    spec = _spec(agent_version="latest")
    spec.resolved_version_for = lambda _agent_id: "0.85.1"
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/npm")

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "E404")

    monkeypatch.setattr(preflight.subprocess, "run", run)
    results = preflight._npm_packages(spec)
    by_name = {result.name: result for result in results}
    failed = by_name["npm package @earendil-works/pi-coding-agent@0.85.1"]
    assert failed.status == "fail"
    assert "not available" in failed.detail


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
    assert calls == []


def test_npm_packages_exact_pins_work_offline(monkeypatch):
    spec = _spec(
        extensions=[SimpleNamespace(kind="npm", package="package@1.0.0")]
    )
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    results = preflight._npm_packages(spec)

    assert all(r.status == "pass" for r in results)


def test_npm_packages_requires_host_npm_for_latest(monkeypatch):
    spec = _spec(agent_version="latest")
    spec.resolved_version_for = lambda _agent_id: "0.85.1"
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    results = preflight._npm_packages(spec)

    assert results[-1].status == "fail"
    assert results[-1].name == "npm"


def test_npm_packages_uses_resolved_latest(monkeypatch):
    spec = _spec(agent_version="latest")
    spec.resolved_version_for = lambda _agent_id: "0.85.1"
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/npm")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '"0.85.1"', "")

    monkeypatch.setattr(preflight.subprocess, "run", run)

    results = preflight._npm_packages(spec)

    assert all(result.status == "pass" for result in results)
    probed = [call[2] for call in calls]
    assert "@earendil-works/pi-coding-agent@0.85.1" in probed


def test_npm_packages_reports_unresolvable_latest(monkeypatch):
    spec = _spec(agent_version="latest")

    def boom(_agent_id):
        raise RuntimeError("cannot resolve latest: npm not on PATH")

    spec.resolved_version_for = boom
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/npm")

    results = preflight._npm_packages(spec)

    assert results[0].status == "fail"
    assert "latest" in results[0].detail
