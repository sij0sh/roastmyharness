"""Pi extension install and health. One job: install/update the Pi extension."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from roast_my_harness import __version__
from roast_my_harness.auth import service as auth_service
from roast_my_harness.extension_install import install_pi_extension
from roast_my_harness.runner import pier as pier_mod
from roast_my_harness.runner import preflight

OBSOLETE_SKILL_NAME = "roastmyharness"

PI_PACKAGE_REPO = "github.com/sij0sh/roastmyharness"


def _settings_mentions_package(settings_path: Path) -> bool:
    try:
        text = settings_path.read_text()
    except OSError:
        return False
    return "roastmyharness" in text


def detect_pi_package(*, home: Path) -> str | None:
    """Return a human-readable Pi-package location when Pi manages the extension."""
    clone = home / ".pi/agent/git" / PI_PACKAGE_REPO
    if (clone / "integrations/pi/roastmyharness.ts").is_file():
        return str(clone)
    settings = home / ".pi/agent/settings.json"
    if _settings_mentions_package(settings):
        return str(settings)
    return None




@dataclass(frozen=True)
class ActionResult:
    name: str
    detail: str
    changed: bool = False
    problem: bool = False


def bundled_root() -> Path | None:
    base = Path(__file__).resolve().parent / "bundled"
    if (base / "integrations/pi/roastmyharness.ts").is_file():
        return base
    return None


def bundled_tasks_root() -> Path | None:
    base = bundled_root()
    if base is None:
        return None
    tasks = base / "tasks/deepswe/tasks"
    return tasks if tasks.is_dir() else None


def repo_root() -> Path | None:
    env = os.environ.get("ROAST_MY_HARNESS_REPO")
    candidates = [Path(env).resolve()] if env else []
    here = Path(__file__).resolve()
    candidates.extend([Path.cwd(), *here.parents])
    for base in candidates:
        if (base / "integrations/pi/roastmyharness.ts").is_file():
            return base
    return bundled_root()


def _tool_visible() -> ActionResult:
    exe = shutil.which("roastmyharness")
    if exe:
        return ActionResult("tool", f"roastmyharness at {exe}")
    module = f"{Path(sys.executable).name} -m roast_my_harness"
    return ActionResult("tool", f"not on PATH; use {module}", problem=True)


def setup(agent: str = "pi", scope: str = "user", *, root: Path | None = None, home: Path | None = None) -> list[ActionResult]:
    if agent != "pi":
        return [ActionResult("agent", f"unknown agent {agent!r}; RoastMyHarness is Pi-only", problem=True)]
    if scope not in ("user", "project"):
        return [ActionResult("scope", f"unknown scope {scope!r}", problem=True)]
    root = root or repo_root()
    home = home or Path.home()
    if root is None:
        return [ActionResult("repo", "repo checkout not found; set ROAST_MY_HARNESS_REPO", problem=True)]
    managed = detect_pi_package(home=home) if scope == "user" else None
    if managed is not None:
        return [
            ActionResult("pi-package", f"managed by Pi at {managed}"),
            ActionResult("engine", "uv owns the engine; run uv tool upgrade"),
            _tool_visible(),
        ]
    base = home / ".pi/agent" if scope == "user" else root / ".pi"
    results = install_pi_extension(
        source_file=root / "integrations/pi/roastmyharness.ts",
        source_dir=root / "integrations/pi/roastmyharness",
        dest_dir=base / "extensions",
    )
    obsolete = base / "skills" / OBSOLETE_SKILL_NAME
    try:
        if obsolete.is_symlink() or obsolete.is_file():
            obsolete.unlink()
            results.append(ActionResult("remove obsolete skill", "removed", changed=True))
    except OSError as error:
        results.append(ActionResult("remove obsolete skill", str(error), problem=True))
    results.append(_tool_visible())
    return results


def detect_agents() -> list[str]:
    return ["pi"] if shutil.which("pi") else []


def run_doctor(*, root: Path | None = None, home: Path | None = None) -> list[preflight.CheckResult]:
    results: list[preflight.CheckResult] = []
    results.append(preflight._ok("python", f"{sys.version.split()[0]} (roastmyharness {__version__})"))
    pi = shutil.which("pi")
    results.append(preflight._ok("pi", pi) if pi else preflight._warn("pi", "pi not on PATH"))
    try:
        exe = pier_mod.pier_executable()
        version = pier_mod.pier_version()
        detail = f"{exe} {version}" if version else f"{exe} (version unreadable)"
        results.append(preflight._ok("pier", detail) if version else preflight._warn("pier", detail))
    except Exception as e:
        results.append(preflight._fail("pier", str(e)))
    results.extend(preflight._docker())
    cred = auth_service.codex_credential()
    if cred is None:
        results.append(preflight._fail("auth", "no codex credential; run pi /login codex"))
    elif auth_service.refresh_hint(cred):
        results.append(preflight._fail("auth", f"codex OAuth expired{auth_service.credential_expiry(cred)}"))
    else:
        results.append(preflight._ok("auth", f"codex OAuth present{auth_service.credential_expiry(cred)}"))
    try:
        models = auth_service.load_host_models()
        providers = ", ".join(sorted(models.get("providers", {}))[:8]) or "none"
        results.append(preflight._ok("model", f"host models.json providers: {providers}"))
    except Exception as e:
        results.append(preflight._warn("model", f"host models.json unusable: {e}"))
    root = root or repo_root()
    home = home or Path.home()
    if pi and root is not None:
        candidates = [
            home / ".pi/agent/extensions/roastmyharness.ts",
            root / ".pi/extensions/roastmyharness.ts",
        ]
        installed = next((p for p in candidates if p.is_file()), None)
        results.append(preflight._ok("extension", str(installed)) if installed
                       else preflight._warn("extension", "not installed; run roastmyharness setup"))
    return results
