"""Pier adapter for oh-my-pi (omp), a pi-family fork.

Subclasses PiAgent and overrides only identity plus omp-specific setup:

- omp ships as a Bun application (engines bun >= 1.3.14), so install
  gains a Bun step before the omp package and version verification.
- omp rejects pi's ``--no-prompt-templates``, ``--no-themes``, and
  ``-nc`` flags; its fairness contract is ``--no-skills`` plus a staged
  ``config.yml`` that disables third-party context-file providers
  (AGENTS.md, CLAUDE.md, and other tools' instruction files) — the
  equivalent of pi's ``-nc``.
- omp reads ``models.yml`` (its migration target for pi's models.json)
  and resolves ``apiKey`` values as bare environment-variable names, not
  pi's ``$VAR`` form. Staging therefore writes models.yml plus a
  model-env.json name list; this adapter resolves those names into the
  run environment.

Imported by pier (``--agent-import-path``): stdlib + pier + this
package's adapter modules only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pier.models.agent.install import AgentInstallSpec, InstallStep

from roast_my_harness.adapter import command as cmd
from roast_my_harness.adapter.pi_agent import (
    PiAgent,
    _validate_pi_version,
    load_variant_manifest,
)

OMP_DISABLED_PROVIDERS: tuple[str, ...] = (
    "agents-md",
    "claude",
    "codex",
    "gemini",
    "cursor",
    "windsurf",
    "cline",
    "opencode",
    "github",
    "vscode",
)

CONFIG_YML = (
    "# Staged by roastmyharness: omp fairness contract.\n"
    "# pi disables context files with -nc; omp needs provider disables.\n"
    "disabledProviders:\n" + "".join(f"  - {p}\n" for p in OMP_DISABLED_PROVIDERS)
)


class OmpAgent(PiAgent):
    """oh-my-pi, driven headless via ``omp --mode json``."""

    PACKAGE: ClassVar[str] = "@oh-my-pi/pi-coding-agent"
    BINARY: ClassVar[str] = "omp"
    FAIRNESS: ClassVar[str] = "--no-skills"

    def __init__(
        self,
        *args: Any,
        variant_manifest: str | None = None,
        thinking: str | None = "high",
        agent_version: str | None = None,
        pi_version: str | None = None,
        **kwargs: Any,
    ) -> None:
        manifest_version = None
        if variant_manifest:
            manifest_version = load_variant_manifest(variant_manifest).get(
                "agent_version"
            )
        version = agent_version or pi_version or manifest_version
        if version is None:
            raise ValueError(
                "OmpAgent requires an exact agent_version pin (--ak "
                "agent_version= or agent_version in the staged manifest)"
            )
        self._pi_version = _validate_pi_version(version)
        super().__init__(
            *args,
            variant_manifest=variant_manifest,
            thinking=thinking,
            pi_version=self._pi_version,
            **kwargs,
        )

    @staticmethod
    def name() -> str:
        return "omp"

    def install_spec(self) -> AgentInstallSpec:
        """Bun first (omp is a Bun app), then the pinned omp package."""
        spec = super().install_spec()
        bun_run = (
            "set -e; npm install -g bun "
            '&& node "$(npm root -g)/bun/install.js" && bun --version'
        )
        return AgentInstallSpec(
            agent_name=spec.agent_name,
            version=spec.version,
            steps=[
                spec.steps[0],
                InstallStep(user="root", run=bun_run),
                *spec.steps[1:],
            ],
            verification_command=spec.verification_command,
        )

    async def setup(self, environment) -> None:
        await super().setup(environment)
        await self.exec_as_agent(
            environment,
            command=(
                f"cat > {cmd.REMOTE_HOME}/config.yml <<'ROAST_EOF'\n"
                f"{CONFIG_YML}ROAST_EOF\n"
            ),
        )

    def _referenced_env_vars(self) -> dict[str, str]:
        """Resolve bare env names staged in model-env.json.

        omp resolves provider apiKeys as bare environment-variable names
        at run time (pi uses $VAR refs in models.json). Staging writes
        the names alongside models.yml; values never touch disk here.
        """
        env_path = self._home_dir / "model-env.json"
        if not env_path.is_file():
            return {}
        try:
            names = json.loads(Path(env_path).read_text())
        except json.JSONDecodeError as e:
            raise ValueError(f"corrupt staged env list {env_path}: {e}") from e
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError(f"staged env list {env_path} must be a JSON string array")
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
                "models.yml references unset environment variables: "
                f"{', '.join(missing)}. Export them on the host or pass them "
                "via pier's --ae KEY=VALUE."
            )
        return env
