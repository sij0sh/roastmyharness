"""Idempotent client integration: ``setup`` and ``doctor``.

setup installs the skill, the Pi extension, and the Claude MCP server
configuration for one agent (pi or claude) and one scope (user or
project). Existing user configuration is preserved: only symlinks that
point elsewhere are replaced, and real files or directories are never
touched. doctor reports Pi, Pier, Docker, auth, model, and integration
health in one place.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from roast_my_harness import __version__
from roast_my_harness.auth import service as auth_service
from roast_my_harness.runner import pier as pier_mod
from roast_my_harness.runner import preflight

SKILL_NAME = "roast-my-harness"
MCP_SERVER_NAME = "roast-my-harness"


@dataclass(frozen=True)
class ActionResult:
    name: str
    detail: str
    changed: bool = False
    problem: bool = False


def repo_root() -> Path | None:
    """Locate a checkout that carries the skill and extension sources."""
    env = os.environ.get("ROAST_MY_HARNESS_REPO")
    candidates = [Path(env).resolve()] if env else []
    here = Path(__file__).resolve()
    candidates.extend([Path.cwd(), *here.parents])
    for base in candidates:
        if (base / ".agents/skills" / SKILL_NAME / "SKILL.md").is_file():
            return base
    return None


def _link(source: Path, dest: Path) -> ActionResult:
    """Point dest at source, replacing only a wrong symlink."""
    label = f"link {dest}"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            if dest.resolve() == source.resolve():
                return ActionResult(label, "already linked")
            dest.unlink()
        elif dest.exists():
            return ActionResult(
                label,
                f"{dest} exists and is not a symlink; left untouched",
                problem=True,
            )
        os.symlink(source, dest)
    except OSError as e:
        return ActionResult(label, str(e), problem=True)
    return ActionResult(label, f"{source} -> {dest}", changed=True)


def mcp_entry() -> dict[str, object]:
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "roast_my_harness.mcp_server"],
    }


def _write_mcp_config(config_path: Path) -> ActionResult:
    """Merge the MCP server entry, preserving every other key."""
    label = f"mcp {config_path}"
    entry = mcp_entry()
    try:
        data: dict = {}
        if config_path.exists():
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                return ActionResult(
                    label,
                    f"{config_path} is not a JSON object; left untouched",
                    problem=True,
                )
            data = loaded
        servers = data.setdefault("mcpServers", {})
        if servers.get(MCP_SERVER_NAME) == entry:
            return ActionResult(label, "already registered")
        servers[MCP_SERVER_NAME] = entry
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError) as e:
        return ActionResult(label, str(e), problem=True)
    return ActionResult(label, "registered", changed=True)


def _tool_visible() -> ActionResult:
    exe = shutil.which("roast-my-harness")
    if exe:
        return ActionResult("tool", f"roast-my-harness at {exe}")
    module = f"{Path(sys.executable).name} -m roast_my_harness"
    return ActionResult("tool", f"not on PATH; use {module} (or `uv tool install .`)", problem=True)


def setup(
    agent: str, scope: str, *, root: Path | None = None, home: Path | None = None
) -> list[ActionResult]:
    """Install integrations for one agent and scope. Idempotent."""
    if agent not in ("pi", "claude"):
        return [ActionResult("agent", f"unknown agent {agent!r}", problem=True)]
    if scope not in ("user", "project"):
        return [ActionResult("scope", f"unknown scope {scope!r}", problem=True)]

    root = root or repo_root()
    home = home or Path.home()
    if root is None:
        return [
            ActionResult(
                "repo",
                "repo checkout not found; set ROAST_MY_HARNESS_REPO to its root",
                problem=True,
            )
        ]

    results: list[ActionResult] = []
    skill_source = root / ".agents/skills" / SKILL_NAME
    extension_source = root / "integrations/pi/roast-my-harness.ts"

    if agent == "pi":
        base = home / ".pi/agent" if scope == "user" else root / ".pi"
        results.append(_link(extension_source, base / "extensions/roast-my-harness.ts"))
        if scope == "user":
            results.append(_link(skill_source, base / "skills" / SKILL_NAME))
        else:
            results.append(
                ActionResult(
                    f"skill {SKILL_NAME}",
                    f"project skill already discovered at {skill_source}",
                )
            )
    else:
        base = home / ".claude" if scope == "user" else root / ".claude"
        results.append(_link(skill_source, base / "skills" / SKILL_NAME))
        config = home / ".claude.json" if scope == "user" else root / ".mcp.json"
        results.append(_write_mcp_config(config))

    results.append(_tool_visible())
    return results


def detect_agents() -> list[str]:
    return [a for a in ("pi", "claude") if shutil.which(a)]


def run_doctor(
    *, root: Path | None = None, home: Path | None = None
) -> list[preflight.CheckResult]:
    """One health table: pi, pier, docker, auth, model, integrations."""
    results: list[preflight.CheckResult] = []
    results.append(
        preflight._ok("python", f"{sys.version.split()[0]} (roast-my-harness {__version__})")
    )

    pi = shutil.which("pi")
    results.append(preflight._ok("pi", pi) if pi else preflight._warn("pi", "pi not on PATH"))

    try:
        exe = pier_mod.pier_executable()
        version = pier_mod.pier_version()
        detail = f"{exe} {version}" if version else f"{exe} (version unreadable)"
        results.append(
            preflight._ok("pier", detail) if version else preflight._warn("pier", detail)
        )
    except Exception as e:
        results.append(preflight._fail("pier", str(e)))

    results.extend(preflight._docker())

    cred = auth_service.codex_credential()
    if cred is None:
        results.append(preflight._fail("auth", "no codex credential; run pi /login codex"))
    elif auth_service.refresh_hint(cred):
        results.append(
            preflight._fail(
                "auth",
                f"codex OAuth expired{auth_service.credential_expiry(cred)}; run pi /login codex",
            )
        )
    else:
        results.append(
            preflight._ok("auth", f"codex OAuth present{auth_service.credential_expiry(cred)}")
        )

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
            home / ".pi/agent/extensions/roast-my-harness.ts",
            root / ".pi/extensions/roast-my-harness.ts",
        ]
        installed = next((p for p in candidates if p.is_symlink() or p.exists()), None)
        results.append(
            preflight._ok("extension", str(installed))
            if installed
            else preflight._warn(
                "extension", "not installed; run roast-my-harness setup --agent pi"
            )
        )

    claude = shutil.which("claude")
    if claude and root is not None:
        claude_skill = home / ".claude/skills" / SKILL_NAME
        found = claude_skill.is_symlink() or (root / ".claude/skills" / SKILL_NAME).is_symlink()
        results.append(
            preflight._ok("skill", str(claude_skill))
            if found
            else preflight._warn("skill", f"not found at {claude_skill}")
        )
        configs = [home / ".claude.json", root / ".mcp.json"]
        registered = False
        for config in configs:
            if not config.is_file():
                continue
            try:
                data = json.loads(config.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and MCP_SERVER_NAME in data.get("mcpServers", {}):
                registered = True
                break
        results.append(
            preflight._ok("mcp", "roast-my-harness server registered")
            if registered
            else preflight._warn("mcp", "not registered; run roast-my-harness setup --agent claude")
        )
    return results
