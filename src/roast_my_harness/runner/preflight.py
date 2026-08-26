"""Preflight checks. Failures block launch; warnings need confirmation."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from roast_my_harness import __version__
from roast_my_harness.auth import service as auth_service
from roast_my_harness.runner import pier as pier_mod
from roast_my_harness.spec.models import ExperimentSpec
from roast_my_harness.tasks.discover import discover_tasks

MIN_FREE_GB = 5.0


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # pass | warn | fail
    detail: str


def _ok(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, "pass", detail)


def _warn(name: str, detail: str) -> CheckResult:
    return CheckResult(name, "warn", detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name, "fail", detail)


def run_checks(spec: ExperimentSpec, *, skip_docker: bool = False) -> list[CheckResult]:
    results: list[CheckResult] = []
    results.append(_python())
    results.append(_pier(spec))
    if not skip_docker:
        results.extend(_docker())
    results.extend(_tasks(spec))
    results.extend(_sources(spec))
    results.extend(_auth(spec))
    results.append(_disk(spec))
    return results


def _python() -> CheckResult:
    # The install floor (requires-python >=3.12) guarantees the minimum;
    # this check reports what is actually running the CLI.
    return _ok("python", f"{sys.version.split()[0]} (roast-my-harness {__version__})")


def _pier(spec: ExperimentSpec) -> CheckResult:
    try:
        exe = pier_mod.pier_executable()
    except Exception as e:
        return _fail("pier", str(e))
    version = pier_mod.pier_version()
    if version is None:
        return _warn("pier", f"found {exe} but could not read version")
    if not pier_mod.version_satisfies(version, spec.pier_version):
        return _fail(
            "pier", f"pier {version} does not satisfy {spec.pier_version}"
        )
    return _ok("pier", f"{exe} {version}")


def _docker() -> list[CheckResult]:
    results = []
    exe = shutil.which("docker")
    if exe is None:
        results.append(_fail("docker", "docker not on PATH"))
        return results
    results.append(_ok("docker", exe))
    try:
        proc = subprocess.run(
            [exe, "compose", "version"], capture_output=True, text=True,
            timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        results.append(_fail("docker compose", str(e)))
        return results
    if proc.returncode != 0:
        results.append(_fail("docker compose", proc.stdout.strip()[:120]))
    else:
        results.append(_ok("docker compose", proc.stdout.strip().splitlines()[0][:80]))
    return results


def _tasks(spec: ExperimentSpec) -> list[CheckResult]:
    results = []
    try:
        tasks = discover_tasks(spec.tasks.path, spec.tasks.include, spec.tasks.exclude)
        results.append(_ok("tasks", f"{len(tasks)} tasks under {spec.tasks.path}"))
    except Exception as e:
        results.append(_fail("tasks", str(e)))
    return results


def _sources(spec: ExperimentSpec) -> list[CheckResult]:
    results = []
    for variant in spec.arms():
        problems: list[str] = []
        for ext in variant.extensions:
            if ext.kind != "local":
                continue
            if not ext.path.is_dir():
                problems.append(f"{variant.id}: extension dir missing {ext.path}")
            elif not (ext.path / ext.entry).is_file():
                problems.append(f"{variant.id}: entry missing {ext.path / ext.entry}")
        for skill in variant.skills:
            if not (skill.path / "SKILL.md").is_file():
                problems.append(f"{variant.id}: SKILL.md missing at {skill.path}")
        if problems:
            results.append(_fail(f"variant {variant.id}", "; ".join(problems)))
        else:
            results.append(
                _ok(
                    f"variant {variant.id}",
                    f"{len(variant.extensions)} ext, "
                    f"{len(variant.skills)} skills",
                )
            )
    return results


def _auth(spec: ExperimentSpec) -> list[CheckResult]:
    results = []
    model = spec.model
    if model.provider == "openai-codex":
        cred = auth_service.codex_credential()
        if cred is None:
            results.append(
                _fail("auth", "no openai-codex credential in pi auth file; run pi /login codex")
            )
        else:
            expires = auth_service.credential_expiry(cred)
            results.append(_ok("auth", f"codex OAuth present{expires}"))
        return results
    if model.provider == "custom":
        if model.models_json is None:
            results.append(_fail("auth", "custom provider requires models_json"))
            return results
        if not model.models_json.is_file():
            results.append(_fail("auth", f"models.json missing: {model.models_json}"))
            return results
        missing = auth_service.missing_env_vars(model.models_json)
        if missing:
            results.append(_fail("auth", f"unset env vars: {', '.join(missing)}"))
        else:
            results.append(_ok("auth", "models.json env vars all set"))
        return results
    # Host-configured provider: block must exist, ids must match, no
    # host-only !command keys, env vars must resolve.
    block = auth_service.host_provider_block(model.provider)
    if block is None:
        results.append(
            _fail("auth", f"provider '{model.provider}' not in host pi models.json")
        )
        return results
    if model.id not in auth_service.host_model_ids(model.provider):
        available = ", ".join(auth_service.host_model_ids(model.provider)[:8])
        results.append(
            _fail("auth", f"model '{model.id}' not defined for host provider "
                          f"'{model.provider}' (available: {available})")
        )
        return results
    if auth_service.has_command_keys(block):
        results.append(
            _fail("auth", f"provider '{model.provider}' uses !command apiKey "
                          "values; host commands cannot run in-container")
        )
        return results
    import json as _json
    import tempfile
    block_text = _json.dumps({"providers": {model.provider: block}})
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        tf.write(block_text)
        block_path = Path(tf.name)
    try:
        missing = auth_service.missing_env_vars(block_path)
    finally:
        block_path.unlink(missing_ok=True)
    if missing:
        results.append(_fail("auth", f"unset env vars: {', '.join(missing)}"))
    else:
        auth_note = ""
        if auth_service.provider_credential(model.provider) is not None:
            auth_note = ", auth entry present"
        results.append(_ok("auth", f"host provider '{model.provider}' configured{auth_note}"))
    return results


def _disk(spec: ExperimentSpec) -> CheckResult:
    usage = shutil.disk_usage(spec.tasks.path if spec.tasks.path.exists() else Path.cwd())
    free_gb = usage.free / 1e9
    if free_gb < MIN_FREE_GB:
        return _fail("disk", f"{free_gb:.1f} GB free, need {MIN_FREE_GB}")
    return _ok("disk", f"{free_gb:.0f} GB free")



def format_table(results: list[CheckResult]) -> str:
    width = max(len(r.name) for r in results) + 2
    lines = []
    for r in results:
        mark = {"pass": "ok", "warn": "WARN", "fail": "FAIL"}[r.status]
        lines.append(f"{r.name:<{width}} {mark:<5} {r.detail}")
    return "\n".join(lines)


def has_failures(results: list[CheckResult]) -> bool:
    return any(r.status == "fail" for r in results)


def has_warnings(results: list[CheckResult]) -> bool:
    return any(r.status == "warn" for r in results)


# Kept honest about the v1 gap: the pi extension-load probe inside a real
# container is not part of preflight yet.
def container_probe_note() -> str:
    return (
        "note: in-container extension load probe not yet automated; run one "
        "smoke task before long experiments"
    )
