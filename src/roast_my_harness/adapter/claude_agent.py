"""Pier adapter for Claude Code under the staged-variant contract.

Imported by pier (``--agent-import-path
roast_my_harness.adapter.claude_agent:RobmyClaude``) and must stay
importable with only pier's dependencies plus the stdlib; the agent
registry is host-side and must never be imported here.

Differences from pier's stock ClaudeCode:
- identity and model validation against the staged variant.json,
- npm-pinned install (no claude.ai download; egress stays npm-only),
- the staged home uploads into pier's CLAUDE_CONFIG_DIR
  (``/logs/agent/sessions``) so sessions land on the host,
- credentials come from the staged per-run env.json (ANTHROPIC_*),
- session discovery also checks that config dir for ATIF conversion.

Kwargs (``--ak key=value``):
    variant_manifest  absolute host path to the staged home's variant.json
    thinking          pi thinking level, mapped onto claude effort flags
    agent_version     npm version of @anthropic-ai/claude-code
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import ClassVar

from pier.agents.installed.claude_code import ClaudeCode
from pier.agents.network import allowlist_from_urls
from pier.environments.base import BaseEnvironment
from pier.models.agent.context import AgentContext
from pier.models.agent.install import AgentInstallSpec, InstallStep
from pier.models.agent.network import NetworkAllowlist
from pier.models.trial.paths import EnvironmentPaths
from pier.utils.trajectory_metrics import populate_context_from_final_metrics

from roast_my_harness.adapter import command as cmd
from roast_my_harness.adapter import setup_handlers
from roast_my_harness.adapter.pi_agent import (
    _validate_pi_version,
    load_variant_manifest,
)

CLAUDE_PACKAGE = "@anthropic-ai/claude-code"

_CREDENTIAL_ENV_KEYS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}


def claude_config_dir(logs_dir: Path) -> Path:
    """Host-side mirror of pier's CLAUDE_CONFIG_DIR for the trial."""
    return logs_dir / "agent" / "sessions"


def _container_config_dir() -> str:
    return (EnvironmentPaths().agent_dir / "sessions").as_posix()


class RobmyClaude(ClaudeCode):
    """Claude Code driven through the same staged-home contract as pi."""

    FAIRNESS: ClassVar[str] = "--strict-mcp-config"

    @staticmethod
    def name() -> str:
        return "claude"

    def __init__(
        self,
        *args,
        variant_manifest: str | None = None,
        thinking: str | None = "high",
        agent_version: str | None = None,
        **kwargs,
    ) -> None:
        if not variant_manifest:
            raise ValueError(
                "RobmyClaude requires --ak variant_manifest=<path> pointing "
                "at the staged home's variant.json"
            )
        self._manifest = load_variant_manifest(variant_manifest)
        self._manifest_path = Path(variant_manifest).resolve()
        if self._manifest.get("agent") != "claude":
            raise ValueError(
                f"variant manifest agent {self._manifest.get('agent')!r} "
                "is not 'claude'"
            )
        self._agent_version = _validate_pi_version(
            agent_version
            if agent_version is not None
            else self._manifest.get("agent_version")
        )
        
        
        kwargs["thinking"] = "disabled" if thinking == "off" else "enabled"
        if thinking in _EFFORT_LEVELS:
            kwargs["reasoning_effort"] = thinking
        super().__init__(*args, version=self._agent_version, **kwargs)
        self._extra_env.update(self._staged_env())
        self._extra_env.update(self._host_env())
        self._validate_model()

    @property
    def _home_dir(self) -> Path:
        return self._manifest_path.parent

    @property
    def _bare_model_id(self) -> str:
        model_id = str(self._manifest.get("model_id", ""))
        return model_id.rsplit("/", 1)[-1]

    def _staged_env(self) -> dict[str, str]:
        """Literal env from the per-run staged env.json (see PiAgent)."""
        env_path = self._manifest_path.parent / "env.json"
        if not env_path.is_file():
            return {}
        try:
            staged = json.loads(env_path.read_text())
        except json.JSONDecodeError as e:
            raise ValueError(f"corrupt staged env file {env_path}: {e}") from e
        if not isinstance(staged, dict):
            return {}
        return {str(k): str(v) for k, v in staged.items()}

    def _host_env(self) -> dict[str, str]:
        """Resolve env_from_host names at execution time (see PiAgent)."""
        names = self._manifest.get("env_from_host") or []
        env: dict[str, str] = {}
        missing: list[str] = []
        for name in names:
            value = self._get_env(str(name))
            if value:
                env[str(name)] = value
            else:
                missing.append(str(name))
        if missing:
            raise ValueError(
                "env_from_host references unset environment variables: "
                f"{', '.join(missing)}. Export them on the host or pass them "
                "via pier's --ae KEY=VALUE."
            )
        return env

    def _validate_model(self) -> None:
        if not self.model_name:
            raise ValueError("RobmyClaude requires a model")
        if self.model_name != self._bare_model_id:
            raise ValueError(
                f"model {self.model_name!r} does not match the staged "
                f"manifest model {self._manifest.get('model_id')!r}"
            )
        settings_path = self._home_dir / "settings.json"
        if settings_path.is_file():
            try:
                settings = json.loads(settings_path.read_text())
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"corrupt staged settings.json {settings_path}: {e}"
                ) from e
            pinned = settings.get("model")
            if pinned is not None and pinned != self._bare_model_id:
                raise ValueError(
                    f"staged settings.json pins model {pinned!r} but the arm "
                    f"runs {self._bare_model_id!r}"
                )
        if not any(self._has_env(key) for key in _CREDENTIAL_ENV_KEYS):
            raise ValueError(
                "no Anthropic credential: stage ANTHROPIC_AUTH_TOKEN or "
                "ANTHROPIC_API_KEY via env.json/env_from_host; host-side "
                "preflight should have caught this"
            )

    def install_spec(self) -> AgentInstallSpec:
        """npm-pinned install; the stock spec downloads from claude.ai."""
        package = f"{CLAUDE_PACKAGE}@{self._agent_version}"
        root_run = (
            "set -e; "
            "if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; "
            "then node --version; "
            "else apt-get update && apt-get install -y nodejs npm; fi"
        )
        agent_run = (
            "set -e; export PATH=\"$HOME/.local/bin:$PATH\"; "
            f"npm install -g {shlex.quote(package)} && claude --version"
        )
        return AgentInstallSpec(
            agent_name=self.name(),
            version=self._agent_version,
            steps=[
                InstallStep(user="root", run=root_run),
                InstallStep(user="root", run=agent_run),
            ],
            verification_command=self.get_version_command(),
        )

    def build_cli_flags(self) -> str:
        flags = super().build_cli_flags()
        return f"{self.FAIRNESS} {flags}" if flags else self.FAIRNESS

    def network_allowlist(self) -> NetworkAllowlist:
        urls = []
        base_url = self._get_env("ANTHROPIC_BASE_URL")
        if base_url:
            urls.append(base_url)
        urls.extend(self._manifest.get("egress_urls") or [])
        return allowlist_from_urls(
            urls, default_domains=setup_handlers.install_domains()
        )

    async def setup(self, environment: BaseEnvironment) -> None:
        await super().setup(environment)
        await self._upload_home(environment)
        for step in self._manifest.get("setup") or []:
            if step.get("handler") == "npm_pi_install":
                raise ValueError("npm_pi_install setup is pi-family only")
            await setup_handlers.run_setup_step(self, environment, step)

    async def _upload_home(self, environment: BaseEnvironment) -> None:
        """Upload the staged home into pier's CLAUDE_CONFIG_DIR.

        The claude-code process both reads pinned settings from and writes
        session transcripts to that dir, so it must exist before run and
        be agent-writable.
        """
        if not self._home_dir.is_dir():
            raise FileNotFoundError(f"staged claude home missing: {self._home_dir}")
        dest = _container_config_dir()
        await environment.upload_dir(self._home_dir, dest)
        await self.exec_as_root(
            environment,
            command=(
                f"chmod -R u+rwX,go+rX {shlex.quote(dest)} && "
                f"chown -R $(id -u agent 2>/dev/null || echo 1000) "
                f"{shlex.quote(dest)}"
            ),
        )

    def _find_session_dir(self, root: Path) -> Path | None:
        projects = root / "projects"
        if not projects.is_dir():
            return None
        dirs = {
            f.parent
            for f in projects.rglob("*.jsonl")
            if "subagents" not in f.parent.parts
        }
        if len(dirs) == 1:
            return next(iter(dirs))
        return None

    def _cost_stream_path(self) -> Path | None:
        for candidate in (
            self.logs_dir / "agent" / "claude-code.txt",
            self.logs_dir / "claude-code.txt",
        ):
            if candidate.is_file():
                return candidate
        return None

    def populate_context_post_run(self, context: AgentContext) -> None:
        session_dir = self._find_session_dir(
            claude_config_dir(self.logs_dir)
        ) or self._find_session_dir(self.logs_dir / "sessions")
        if session_dir is None:
            self.logger.debug("No Claude Code session directory found")
            return
        try:
            trajectory = self._convert_events_to_trajectory(session_dir)
        except Exception as exc:
            self.logger.debug(f"Failed to convert Claude Code events: {exc}")
            return
        if trajectory is None:
            self.logger.debug("Claude Code session produced no trajectory")
            return
        trajectory_path = self.logs_dir / cmd.TRAJECTORY_FILENAME
        try:
            trajectory_path.write_text(
                json.dumps(trajectory.to_json_dict(), indent=2, ensure_ascii=False)
            )
        except OSError as exc:
            self.logger.warning(f"Failed to write trajectory file: {exc}")
            return
        if trajectory.final_metrics:
            populate_context_from_final_metrics(context, trajectory.final_metrics)
