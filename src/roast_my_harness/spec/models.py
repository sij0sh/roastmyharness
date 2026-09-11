"""Pydantic v2 schema for TOML experiment files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from roast_my_harness.adapter.registry import get_agent
from roast_my_harness.adapter.versions import LATEST, resolve_package_version, validate_agent_pin
from roast_my_harness.constants import DEFAULT_PI_VERSION, FAIRNESS_FLAGS
from roast_my_harness.evals.registry import BUNDLED_EVAL_IDS
from roast_my_harness.observability import SECRET_KEY_WORDS

SCHEMA_VERSION = 2
RESERVED_VARIANT_IDS = {"control"}


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

ALLOWED_PI_FLAGS = {
    "--append-system-prompt",
    "--system-prompt",
    "--tools",
    "--exclude-tools",
    "--no-builtin-tools",
    "--no-tools",
}


_FAIRNESS_FLAG_NAMES = frozenset(FAIRNESS_FLAGS.split())
_CONSTRUCTION_PI_FLAGS = {
    "--no-context-files",
    "--model",
    "--thinking",
    "--skill",
    "--session-dir",
    "--mode",
    "--extension",
    "--no-extensions",
}
RESERVED_PI_FLAGS = _FAIRNESS_FLAG_NAMES | _CONSTRUCTION_PI_FLAGS


def _safe_relative_component(value: str, field: str) -> str:
    """One slug-safe destination component: no separators, no dot specials."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise ValueError(
            f"{field} must be a slug-safe name (letters, digits, '.', '_', '-'; "
            f"no leading dot), got {value!r}"
        )
    return value


def _safe_rel_path(value: str, field: str) -> str:
    """A strictly relative path usable under a home directory."""
    if value.startswith(("/", "\\")) or Path(value).is_absolute():
        raise ValueError(f"{field} must be relative, got {value!r}")
    parts = value.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{field} must not contain '..', '.', or empty components: {value!r}")
    return value


def _agent_version_pin(value: str, field: str) -> str:
    """A pi-family agent pin: 'latest' or an exact version safe as one argv token."""
    return validate_agent_pin(value, field)


def _safe_env_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"environment variable name {value!r} must be UPPER_SNAKE_CASE")
    return value


ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]


class ResolvedModelSpec(BaseModel):
    """Host-config materialization: what actually runs, recorded at load
    time so spec_hash covers host configuration drift."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    provider_block_sha256: str
    env_vars: list[str] = Field(default_factory=list)


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = "gpt-5.6-luna"
    provider: str = "openai-codex"
    provider_id: str | None = None
    models_json: Path | None = None
    resolved_model: ResolvedModelSpec | None = None

    def full_id(self) -> str:
        """The complete provider/model string used for Pier's --model."""
        if self.provider == "custom":
            if not self.provider_id:
                raise ValueError("custom provider requires provider_id")
            return f"{self.provider_id}/{self.id}"
        return f"{self.provider}/{self.id}"

    @model_validator(mode="after")
    def _require_custom_fields(self) -> ModelSpec:
        if self.provider == "custom":
            if not self.provider_id:
                raise ValueError("provider 'custom' requires provider_id")
            if not self.models_json:
                raise ValueError("provider 'custom' requires models_json")
        return self


class LocalExtension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["local"]
    path: Path
    entry: str
    name: str | None = None
    exclude: list[str] = Field(default_factory=list)
    runtime_packages: list[str] = Field(default_factory=list)

    @field_validator("entry")
    @classmethod
    def _safe_entry(cls, value: str) -> str:
        return _safe_rel_path(value, "extension entry")

    @field_validator("name")
    @classmethod
    def _safe_name(cls, value: str | None) -> str | None:
        return _safe_relative_component(value, "extension name") if value else value

    @field_validator("runtime_packages")
    @classmethod
    def _safe_packages(cls, value: list[str]) -> list[str]:
        for name in value:
            segments = name.split("/")
            if len(segments) > 2 or not all(
                re.fullmatch(r"@?[A-Za-z0-9][A-Za-z0-9._-]*", segment) for segment in segments
            ):
                raise ValueError(f"runtime_package {name!r} must be a plain npm package name")
        return value


class NpmExtension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["npm"]
    package: str
    name: str | None = None

    @field_validator("name")
    @classmethod
    def _safe_name(cls, value: str | None) -> str | None:
        return _safe_relative_component(value, "extension name") if value else value

    @field_validator("package")
    @classmethod
    def _exact_version(cls, value: str) -> str:
        name, sep, version = value.rpartition("@")
        if not (
            sep
            and re.fullmatch(r"@?[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?", name)
            and re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.-]+)*", version)
        ):
            raise ValueError(
                f"npm package must pin an exact version, got {value!r} (expected name@x.y.z)"
            )
        return value


ExtensionSpec = Annotated[LocalExtension | NpmExtension, Field(discriminator="kind")]


class SkillSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["local"] = "local"
    path: Path
    name: str | None = None

    @field_validator("name")
    @classmethod
    def _safe_name(cls, value: str | None) -> str | None:
        return _safe_relative_component(value, "skill name") if value else value


class ContextFileSpec(BaseModel):
    """One explicit repo instruction file delivered as variant context.

    kind "agents" means the file plays the AGENTS.md role: the adapter
    prepends its content to the trial instruction while the fairness
    contract (-nc / disabledProviders) keeps stripping implicit copies
    from repos and homes. Only explicitly declared files are delivered.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["agents"] = "agents"
    path: Path
    name: str | None = None

    @field_validator("name")
    @classmethod
    def _safe_name(cls, value: str | None) -> str | None:
        return _safe_relative_component(value, "context file name") if value else value


class NpmPiInstall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handler: Literal["npm_pi_install"]
    package: str

    @field_validator("package")
    @classmethod
    def _exact_version(cls, value: str) -> str:
        name, separator, version = value.rpartition("@")
        if not (
            separator
            and re.fullmatch(r"@?[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?", name)
            and re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.-]+)*", version)
        ):
            raise ValueError(f"npm_pi_install requires an exact package pin, got {value!r}")
        return value


class InstallBinary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handler: Literal["install_binary"]
    source: Path
    destination: str = "/usr/local/bin"
    verify: str | None = None


class RunRtkInit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handler: Literal["run_rtk_init"]


class CodegraphIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handler: Literal["codegraph_index"]
    bundle: Path


class SnoopIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handler: Literal["snoop_index"]
    binary: Path


SetupSpec = Annotated[
    NpmPiInstall | InstallBinary | RunRtkInit | CodegraphIndex | SnoopIndex,
    Field(discriminator="handler"),
]


class VariantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    agent: str | None = None
    model: ModelSpec | None = None
    extensions: list[ExtensionSpec] = Field(default_factory=list)
    skills: list[SkillSpec] = Field(default_factory=list)
    context_files: list[ContextFileSpec] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_from_host: list[str] = Field(default_factory=list)
    setup: list[SetupSpec] = Field(default_factory=list)
    egress_urls: list[str] = Field(default_factory=list)
    pi_flags: list[str] = Field(default_factory=list)
    runtime_agent_install: bool = False

    @field_validator("id")
    @classmethod
    def _safe_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError(
                f"variant id {value!r} must be lowercase alphanumeric/hyphen, starting alphanumeric"
            )
        return value

    @field_validator("agent")
    @classmethod
    def _known_agent(cls, value: str | None) -> str | None:
        return value if value is None else get_agent(value).id

    @field_validator("env")
    @classmethod
    def _no_secret_literals(cls, value: dict[str, str]) -> dict[str, str]:
        from roast_my_harness.observability import contains_secret

        for key, item in value.items():
            _safe_env_name(key)
            lowered = key.lower().replace("-", "_")
            if any(word in lowered for word in SECRET_KEY_WORDS):
                raise ValueError(
                    f"env key {key!r} looks like a credential; pass secrets via "
                    'env_from_host = ["NAME"] instead of literal values'
                )
            if contains_secret(item):
                raise ValueError(
                    f"env value for {key!r} looks like a credential; pass secrets "
                    'via env_from_host = ["NAME"] instead of literal values'
                )
        return value

    @field_validator("env_from_host")
    @classmethod
    def _safe_host_names(cls, value: list[str]) -> list[str]:
        for name in value:
            _safe_env_name(name)
        return value

    @field_validator("egress_urls")
    @classmethod
    def _https_only(cls, value: list[str]) -> list[str]:
        for url in value:
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError(f"egress_urls must be absolute https:// URLs, got {url!r}")
        return value

    @field_validator("pi_flags")
    @classmethod
    def _allowlisted_flags(cls, value: list[str]) -> list[str]:
        for flag in value:
            if any(ch.isspace() for ch in flag):
                raise ValueError(
                    f"pi_flags entries must be single tokens, got {flag!r}; use --flag=value form"
                )
            name = flag.split("=", 1)[0]
            if name in RESERVED_PI_FLAGS:
                raise ValueError(
                    f"pi_flags entry {flag!r} conflicts with a harness-controlled "
                    "flag and would break arm fairness"
                )
            if name not in ALLOWED_PI_FLAGS:
                raise ValueError(
                    f"pi_flags entry {flag!r} is not allowlisted; allowed: "
                    f"{', '.join(sorted(ALLOWED_PI_FLAGS))}"
                )
        return value


class TaskSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    include: list[str] = Field(default_factory=lambda: ["*"])
    exclude: list[str] = Field(default_factory=list)
    preset: str | None = None
    """Named task list from the benchmark catalog (e.g. "luna-signal").

    The preset provides the base set; include/exclude globs filter it
    further. Resolved at load time into include, so the effective set is
    visible in the spec dump and covered by run identity.
    """


class EvalSpec(BaseModel):
    """Which evaluation this experiment runs.

    type bundled selects a harness-shipped eval (today only deepswe);
    generated selects a frozen custom eval described by eval.toml beside
    the task root; external selects a plain local Pier task set with an
    optional eval.toml. id defaults to deepswe for bundled selections;
    every other type requires an explicit id. revision pins the eval
    revision; for generated evals it must match the descriptor.
    A missing [evaluation] block means the legacy default
    (bundled DeepSWE) and keeps legacy run identity.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["bundled", "generated", "external"] = "bundled"
    id: str | None = None
    revision: str | None = None

    @field_validator("id")
    @classmethod
    def _safe_id(cls, value: str | None) -> str | None:
        return _safe_relative_component(value, "evaluation id") if value else value

    @model_validator(mode="after")
    def _require_known_selection(self) -> EvalSpec:
        eval_id = self.id or "deepswe"
        if self.type == "bundled" and eval_id not in BUNDLED_EVAL_IDS:
            raise ValueError(
                f"unknown bundled eval {eval_id!r} "
                f"(available: {', '.join(sorted(BUNDLED_EVAL_IDS)) or 'none'})"
            )
        if self.type != "bundled" and not self.id:
            raise ValueError(f"evaluation.id is required for type {self.type!r}")
        return self


class ControlSpec(BaseModel):
    """Bare-agent control arm with deterministic historic-reuse policy.

    mode fresh runs every control trial; mode historic reuses eligible
    history, with history_scope intersection running only the
    history-backed test intersection and hybrid running fresh controls
    for tasks without eligible history. There is deliberately no stored
    interactive policy: the wizard asks, the spec records the answer.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    agent: str | None = None
    mode: Literal["fresh", "historic"] = "fresh"
    history_scope: Literal["intersection", "hybrid"] = "hybrid"
    minimum_runs_per_task: int = Field(default=4, ge=1)
    maximum_age_days: int = Field(default=30, ge=1)
    sentinel_tasks: int = Field(default=4, ge=0)
    on_drift: Literal["fresh", "abort"] = "fresh"
    on_inconclusive: Literal["fresh", "abort"] = "fresh"

    @field_validator("agent")
    @classmethod
    def _known_agent(cls, value: str | None) -> str | None:
        return value if value is None else get_agent(value).id


class ConcurrencySpec(BaseModel):
    """Concurrency bounds.

    max_parallel caps total trials across all launching arms; when set,
    per_variant is divided down so arms * effective_per_variant <= max_parallel.
    launch_max_in_flight bounds simultaneous arm starts (admission gate);
    launch_stagger_sec spaces starts to break simultaneity. quota_max_parallel
    optionally derates peak concurrency to a provider quota tier (operator-set
    after the first production 429; None means no derating).
    Decision: both ceiling and stagger enabled by default; quota derating
    opt-in per experiment until throttle telemetry fixes the tier.
    """

    model_config = ConfigDict(extra="forbid")

    per_variant: int = Field(default=2, ge=1, le=16)
    max_parallel: int | None = Field(default=None, ge=1, le=32)
    launch_max_in_flight: int = Field(default=8, ge=1, le=32)
    launch_stagger_sec: float = Field(default=0.5, ge=0.0, le=10.0)
    quota_max_parallel: int | None = Field(default=None, ge=1, le=32)

    def effective_per_variant(self, launching_arms: int) -> int:
        """Per-arm concurrency honoring max_parallel and quota caps."""
        per = self.per_variant
        if launching_arms >= 1 and self.max_parallel is not None:
            per = max(1, min(per, self.max_parallel // launching_arms))
        if launching_arms >= 1 and self.quota_max_parallel is not None:
            per = max(1, min(per, self.quota_max_parallel // launching_arms))
        return per

    def peak_parallel(self, launching_arms: int) -> int:
        """Peak total concurrency given the arms launching at once."""
        arms = max(launching_arms, 1)
        return self.effective_per_variant(arms) * arms


MAX_VARIANTS = 16
MAX_REPETITIONS = 16


class ExecutionSpec(BaseModel):
    """Repetitions are independent scored rollouts; retries are attempts.

    Trial identity is (variant, task, replicate); attempt retries one
    trial after infra/error. max_retries caps relaunches of an errored
    trial via the retry-errors path (the initial attempt always runs).
    """

    model_config = ConfigDict(extra="forbid")

    repetitions: int = Field(default=1, ge=1, le=MAX_REPETITIONS)
    max_retries: int = Field(default=1, ge=0, le=MAX_REPETITIONS)


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    name: str
    hypothesis: str = Field(
        default="",
        description=(
            "Falsifiable pre-run statement frozen at authoring time; "
            "annotation only, excluded from run identity hashes."
        ),
    )
    model: ModelSpec = Field(default_factory=ModelSpec)
    thinking: ThinkingLevel = "high"
    execution: ExecutionSpec = Field(default_factory=ExecutionSpec)
    agent: str = "pi"
    agent_versions: dict[str, str] = Field(default_factory=dict)
    agent_version: str | None = None
    pi_version: str = DEFAULT_PI_VERSION
    pier_version: str = ">=0.3,<0.4"
    tasks: TaskSelection
    evaluation: EvalSpec | None = None
    control: ControlSpec | None = None
    variants: list[VariantSpec] = Field(default_factory=list)
    concurrency: ConcurrencySpec = Field(default_factory=ConcurrencySpec)

    @field_validator("agent")
    @classmethod
    def _known_agent(cls, value: str) -> str:
        return get_agent(value).id

    @field_validator("pi_version")
    @classmethod
    def _safe_pi_version(cls, value: str) -> str:
        return _agent_version_pin(value, "pi_version")

    @field_validator("agent_version")
    @classmethod
    def _safe_agent_version(cls, value: str | None) -> str | None:
        return None if value is None else _agent_version_pin(value, "agent_version")

    @field_validator("agent_versions")
    @classmethod
    def _safe_agent_versions(cls, value: dict[str, str]) -> dict[str, str]:
        for agent_id, pin in value.items():
            get_agent(agent_id)
            _agent_version_pin(pin, f"agent_versions.{agent_id}")
        return value

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value == 1:
            raise ValueError(
                "schema_version 1 is no longer accepted: set "
                "schema_version = 2. v2 freezes agent versions at prepare "
                "time (run identity covers resolved versions, so a moved "
                "'latest' starts a new run) and defaults repetitions to 1; "
                'a stored control reuse = "ask" must become an explicit '
                'mode = "fresh" or "historic" choice'
            )
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value}, expected {SCHEMA_VERSION}")
        return value

    @model_validator(mode="after")
    def _require_arms_and_unique_ids(self) -> ExperimentSpec:
        has_control = self.control is not None and self.control.enabled
        if not self.variants and not has_control:
            raise ValueError("experiment needs at least one variant or an enabled control arm")
        ids = [v.id for v in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate variant ids: {sorted(ids)}")
        reserved = sorted(set(ids) & RESERVED_VARIANT_IDS)
        if reserved:
            raise ValueError(f"variant ids reserved for the control arm: {reserved}")
        return self

    @field_validator("variants")
    @classmethod
    def _cap_variants(cls, value: list[VariantSpec]) -> list[VariantSpec]:
        if len(value) > MAX_VARIANTS:
            raise ValueError(f"experiment allows at most {MAX_VARIANTS} variants, got {len(value)}")
        return value

    def resolved_agents(self) -> dict[str, str]:
        """Arm id -> agent id for every launched arm."""
        agents: dict[str, str] = {}
        if self.control is not None and self.control.enabled:
            agents["control"] = self.control.agent or self.agent
        for variant in self.variants:
            agents[variant.id] = variant.agent or self.agent
        return agents

    def agent_version_for(self, agent_id: str) -> str:
        """Pinned version for one agent: 'latest' or an exact version.

        Precedence: per-agent agent_versions map, then deprecated aliases
        (agent_version for the default agent, pi_version for pi), then the
        registry default. Owner: spec. Decision: alias-bridge migration;
        new pins use agent_versions, aliases stay until removal.
        """
        if agent_id in self.agent_versions:
            return self.agent_versions[agent_id]
        if self.agent_version is not None and agent_id == self.agent:
            return self.agent_version
        if agent_id == "pi":
            return self.pi_version
        return get_agent(agent_id).default_version

    def resolved_version_for(self, agent_id: str) -> str:
        """Exact version one agent installs.

        'latest' pins query the npm registry, so every run picks up the
        newest release; exact pins return unchanged. Call this wherever
        the version is consumed (hash, home, launch, probe, preflight) and
        keep agent_version_for for the stable spec pin.
        """
        pin = self.agent_version_for(agent_id)
        if pin == LATEST:
            return resolve_package_version(get_agent(agent_id).npm_package, pin)
        return pin

    def arms(self) -> list[VariantSpec]:
        """Every launched arm with agent inheritance applied. The control is
        a bare VariantSpec named control."""
        arms: list[VariantSpec] = []
        if self.control is not None and self.control.enabled:
            arms.append(
                VariantSpec(
                    id="control",
                    name="Bare control",
                    agent=self.control.agent or self.agent,
                )
            )
        arms.extend(
            v if v.agent is not None else v.model_copy(update={"agent": self.agent})
            for v in self.variants
        )
        return arms

    def model_for(self, variant: VariantSpec) -> ModelSpec:
        """The arm's effective model: the variant override when set, else
        the global [model]."""
        return variant.model or self.model

    @model_validator(mode="after")
    def _validate_agent_arms(self) -> ExperimentSpec:
        if (
            self.agent_version is not None
            and self.agent == "pi"
            and self.agent_version != self.pi_version
        ):
            raise ValueError(
                f"agent_version {self.agent_version!r} conflicts with "
                f"pi_version {self.pi_version!r}; set one version pin only"
            )
        if self.agent in self.agent_versions:
            if (
                self.agent_version is not None
                and self.agent_versions[self.agent] != self.agent_version
            ):
                raise ValueError(
                    f"agent_versions[{self.agent!r}] conflicts with agent_version; "
                    "set one version pin only"
                )
            if self.agent == "pi" and self.agent_versions["pi"] != self.pi_version:
                raise ValueError(
                    "agent_versions['pi'] conflicts with pi_version; set one version pin only"
                )
        if "pi" in self.agent_versions and self.agent != "pi":
            if self.agent_versions["pi"] != self.pi_version:
                raise ValueError(
                    "agent_versions['pi'] conflicts with pi_version; set one version pin only"
                )
        for variant in self.variants:
            agent = get_agent(variant.agent or self.agent)
            if variant.context_files and not agent.supports_context_files:
                raise ValueError(
                    f"variant {variant.id!r} declares context_files but agent "
                    f"{agent.id!r} cannot deliver explicit context files"
                )
            if agent.supports_pi_features:
                continue
            pi_only = [
                feature
                for feature, values in (
                    ("extensions", variant.extensions),
                    ("skills", variant.skills),
                    ("pi_flags", variant.pi_flags),
                )
                if values
            ]
            if any(step.handler == "npm_pi_install" for step in variant.setup):
                pi_only.append("npm_pi_install setup")
            if pi_only:
                raise ValueError(
                    f"variant {variant.id!r} runs agent {agent.id!r} which "
                    f"does not support pi-only features: {', '.join(pi_only)}"
                )
        return self

    def peak_concurrency(self) -> int:
        """Peak total trials in flight when all arms launch at once.

        Each (arm, replicate) launches its own pier process, so
        repetitions multiply the launching units.
        """
        return self.concurrency.peak_parallel(len(self.arms()) * max(self.execution.repetitions, 1))
