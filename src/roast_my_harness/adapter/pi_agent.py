"""Spec-driven Pier adapter for the pi coding agent.

The adapter is data-driven: it reads variant.json from the staged home
uploaded per job and contains no variant names. It is imported by pier
(``--agent-import-path roast_my_harness.adapter.pi_agent:PiAgent``) and must
therefore stay importable with only pier's own dependencies plus the
stdlib.

Kwargs (``--ak key=value``):
    variant_manifest  absolute host path to the staged home's variant.json
    thinking          pi --thinking level (default: high)
    pi_version        npm version of @earendil-works/pi-coding-agent
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar

from pier.agents.installed.base import (
    BaseInstalledAgent,
    with_prompt_template,
)
from pier.agents.network import allowlist_from_urls, collect_url_values
from pier.environments.base import BaseEnvironment
from pier.models.agent.context import AgentContext
from pier.models.agent.install import AgentInstallSpec, InstallStep
from pier.models.agent.network import NetworkAllowlist
from pier.models.trajectories import FinalMetrics
from pier.utils.trajectory_metrics import populate_context_from_final_metrics

from roast_my_harness.adapter import command as cmd
from roast_my_harness.adapter import setup_handlers
from roast_my_harness.adapter.atif import write_trajectory

PI_PACKAGE = "@earendil-works/pi-coding-agent"
DEFAULT_PI_NPM_VERSION = "0.84.3"


_BUILTIN_PI_PROVIDERS = {"openai-codex"}
_BUILTIN_PI_URLS = {
    "https://chatgpt.com/backend-api",
    "https://auth.openai.com",
}

_BASE_RUN_ENV = {"NODE_USE_ENV_PROXY": "1"}


def load_variant_manifest(path: str) -> dict[str, Any]:
    """Load and validate variant.json (stdlib only)."""
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"variant manifest missing: {manifest_path}. The runner stages it "
            "before launch; pass --ak variant_manifest=<path>."
        )
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"corrupt variant manifest {manifest_path}: {e}") from e
    required = ("variant_id", "variant_hash", "pi_version", "model_id")
    missing = [key for key in required if key not in manifest]
    if missing:
        raise ValueError(
            f"variant manifest {manifest_path} missing keys: {', '.join(missing)}"
        )
    return manifest


class PiAgent(BaseInstalledAgent):
    """The pi coding agent, driven headless via ``pi --mode json``."""

    SUPPORTS_ATIF: ClassVar[bool] = True

    def __init__(
        self,
        *args: Any,
        variant_manifest: str | None = None,
        thinking: str | None = "high",
        pi_version: str | None = DEFAULT_PI_NPM_VERSION,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if not variant_manifest:
            raise ValueError(
                "PiAgent requires --ak variant_manifest=<path> pointing at the "
                "staged home's variant.json"
            )
        self._manifest = load_variant_manifest(variant_manifest)
        self._manifest_path = Path(variant_manifest).resolve()
        self._thinking = thinking
        self._pi_version = pi_version or self._manifest.get("pi_version")
        self._instruction: str | None = None
        self._validate_model()

    @property
    def _home_dir(self) -> Path:
        return self._manifest_path.parent

    @staticmethod
    def name() -> str:
        return "pi"

    def get_version_command(self) -> str | None:
        return "pi --version"

    def parse_version(self, stdout: str) -> str:
        return stdout.strip().splitlines()[-1].strip() if stdout.strip() else "unknown"

    # ------------------------------------------------------------ model ---

    def _models_config_path(self) -> Path | None:
        path = self._home_dir / "models.json"
        return path if path.is_file() else None

    def _load_models_config(self) -> dict[str, Any] | None:
        path = self._models_config_path()
        if path is None:
            return None
        return json.loads(path.read_text())

    def _staged_providers(self) -> set[str]:
        """Providers declared by staged files: models.json blocks and
        auth.json entries. No hardcoded built-in list."""
        providers: set[str] = set()
        config = self._load_models_config()
        if config is not None:
            providers.update(config.get("providers", {}).keys())
        auth_path = self._home_dir / "auth.json"
        if auth_path.is_file():
            try:
                providers.update(json.loads(auth_path.read_text()).keys())
            except (json.JSONDecodeError, OSError):
                pass
        return providers

    def _validate_model(self) -> None:
        if not self.model_name or "/" not in self.model_name:
            raise ValueError(
                "Model name must be 'provider/model' (e.g. "
                "'openai-codex/gpt-5.6-luna')"
            )
        provider, model = self.model_name.split("/", 1)
        staged = self._staged_providers()
        if provider in staged:
            # Models.json blocks declare their model ids; auth-only
            # providers rely on pi's own catalog and id validation.
            config = self._load_models_config()
            if config is not None and provider in config.get("providers", {}):
                model_ids = [
                    m.get("id")
                    for m in config["providers"][provider].get("models", [])
                ]
                if model not in model_ids:
                    raise ValueError(
                        f"Model '{model}' not defined for provider '{provider}' "
                        f"(available: {', '.join(str(i) for i in model_ids)})"
                    )
            return
        raise ValueError(
            f"Provider '{provider}' has neither a staged models.json block "
            f"nor a staged auth entry in {self._home_dir}; host-side "
            f"preflight should have caught this"
        )

    def _referenced_env_vars(self) -> dict[str, str]:
        """Resolve $VAR / ${VAR} references in the staged models.json.

        Resolution order per var: pier agent env (--ae) first, then the
        host environment. Values never appear in logs.
        """
        config = self._load_models_config()
        if config is None:
            return {}
        text = json.dumps(config)
        names = sorted(set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", text)))
        env: dict[str, str] = {}
        missing: list[str] = []
        for name in names:
            value = self._get_env(name)
            if value:
                env[name] = value
            else:
                missing.append(name)
        if missing:
            raise ValueError(
                "models.json references unset environment variables: "
                f"{', '.join(missing)}. Export them on the host or pass them "
                "via pier's --ae KEY=VALUE."
            )
        return env

    # ---------------------------------------------------------- install ---

    def install_spec(self) -> AgentInstallSpec:
        version_spec = f"@{self._pi_version}" if self._pi_version else ""
        root_run = (
            "set -e; "
            "if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; "
            "then node --version; "
            "else apt-get update && apt-get install -y nodejs npm; fi"
        )
        agent_run = f"set -e; npm install -g {PI_PACKAGE}{version_spec} && pi --version"
        return AgentInstallSpec(
            agent_name=self.name(),
            version=self._pi_version,
            steps=[
                InstallStep(user="root", run=root_run),
                InstallStep(user="root", run=agent_run),
            ],
            verification_command=self.get_version_command(),
        )

    def network_allowlist(self) -> NetworkAllowlist:
        urls: list[str] = []
        config = self._load_models_config()
        if config is not None:
            urls.extend(collect_url_values(config))
            urls.extend(collect_url_values(config, keys={"baseUrl"}))
        provider = self.model_name.split("/", 1)[0] if self.model_name else ""
        if provider in _BUILTIN_PI_PROVIDERS:
            urls.extend(_BUILTIN_PI_URLS)
        urls.extend(self._manifest.get("egress_urls") or [])
        return allowlist_from_urls(
            urls, default_domains=setup_handlers.install_domains()
        )

    # ------------------------------------------------------------ setup ---

    async def setup(self, environment: BaseEnvironment) -> None:
        await super().setup(environment)
        await self._upload_home(environment)
        for package in self._manifest.get("npm_packages") or []:
            await setup_handlers.npm_pi_install(
                self, environment, {"package": package}
            )
        for step in self._manifest.get("setup") or []:
            await setup_handlers.run_setup_step(self, environment, step)

    async def _upload_home(self, environment: BaseEnvironment) -> None:
        """Upload the staged home world-readable, auth.json agent-writable.

        Runs in setup (not run) so variant setup steps can install into the
        home without charging the agent's timed execution phase.
        """
        if not self._home_dir.is_dir():
            raise FileNotFoundError(f"staged pi home missing: {self._home_dir}")
        await environment.upload_dir(self._home_dir, cmd.REMOTE_HOME)
        await self.exec_as_root(
            environment,
            command=f"chmod -R a+rX {cmd.REMOTE_HOME}",
        )
        auth = cmd.REMOTE_HOME + "/auth.json"
        await self.exec_as_root(
            environment,
            command=(
                f"if [ -f {auth} ]; then chown $(id -u agent 2>/dev/null || "
                f"echo 1000) {auth} 2>/dev/null || true; "
                f"chmod 0600 {auth} || true; fi"
            ),
        )

    # -------------------------------------------------------------- run ---

    @with_prompt_template
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self._instruction = instruction
        run_env = dict(_BASE_RUN_ENV)
        run_env.update(self._referenced_env_vars())
        run_env.update(self._manifest.get("env") or {})
        extra_flags = list(self._manifest.get("pi_flags") or [])
        skills = [s.get("path", "") for s in self._manifest.get("skills") or []]
        command = cmd.build_run_command(
            model=self.model_name or "",
            instruction=instruction,
            thinking=self._thinking,
            skill_paths=[s for s in skills if s],
            extra_flags=extra_flags,
        )
        await self.exec_as_agent(environment, command=command, env=run_env)

    # ---------------------------------------------------- post-run ATIF ---

    def populate_context_post_run(self, context: AgentContext) -> None:
        events_path = self.logs_dir / cmd.EVENTS_FILENAME
        if not events_path.exists():
            self.logger.debug(f"No pi event stream at {events_path}")
            return
        try:
            trajectory = write_trajectory(
                events_path,
                self.logs_dir / cmd.TRAJECTORY_FILENAME,
                instruction=self._instruction or "",
                variant=self._manifest.get("variant_id", "unknown"),
                agent_version=self._pi_version or "unknown",
            )
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning(f"Corrupt pi event stream: {e}")
            return
        final_metrics: FinalMetrics | None = trajectory.final_metrics
        if final_metrics:
            populate_context_from_final_metrics(context, final_metrics)
