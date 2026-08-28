"""Pydantic v2 schema for TOML and YAML experiment files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from roast_my_harness.constants import DEFAULT_PI_VERSION, FAIRNESS_FLAGS
from roast_my_harness.observability import SECRET_KEY_WORDS

SCHEMA_VERSION = 1
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
        raise ValueError(
            f"{field} must not contain '..', '.', or empty components: {value!r}"
        )
    return value


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
                re.fullmatch(r"@?[A-Za-z0-9][A-Za-z0-9._-]*", segment)
                for segment in segments
            ):
                raise ValueError(
                    f"runtime_package {name!r} must be a plain npm package name"
                )
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
                f"npm package must pin an exact version, got {value!r} "
                "(expected name@x.y.z)"
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
            raise ValueError(
                f"npm_pi_install requires an exact package pin, got {value!r}"
            )
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


SetupSpec = Annotated[
    NpmPiInstall | InstallBinary | RunRtkInit | CodegraphIndex,
    Field(discriminator="handler"),
]


class VariantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    extensions: list[ExtensionSpec] = Field(default_factory=list)
    skills: list[SkillSpec] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_from_host: list[str] = Field(default_factory=list)
    setup: list[SetupSpec] = Field(default_factory=list)
    egress_urls: list[str] = Field(default_factory=list)
    pi_flags: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _safe_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError(
                f"variant id {value!r} must be lowercase alphanumeric/hyphen, "
                "starting alphanumeric"
            )
        return value

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
                raise ValueError(
                    f"egress_urls must be absolute https:// URLs, got {url!r}"
                )
        return value

    @field_validator("pi_flags")
    @classmethod
    def _allowlisted_flags(cls, value: list[str]) -> list[str]:
        for flag in value:
            if any(ch.isspace() for ch in flag):
                raise ValueError(
                    f"pi_flags entries must be single tokens, got {flag!r}; "
                    "use --flag=value form"
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


class ControlSpec(BaseModel):
    """Bare Pi control arm. Controls always run fresh."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class ConcurrencySpec(BaseModel):
    """Concurrency bounds.

    max_parallel caps total trials across all launching arms; when set,
    per_variant is divided down so arms * effective_per_variant <= max_parallel.
    """

    model_config = ConfigDict(extra="forbid")

    per_variant: int = Field(default=2, ge=1, le=16)
    max_parallel: int | None = Field(default=None, ge=1, le=32)

    def effective_per_variant(self, launching_arms: int) -> int:
        """Per-arm concurrency honoring the global max_parallel cap."""
        if launching_arms < 1 or self.max_parallel is None:
            return self.per_variant
        return max(1, min(self.per_variant, self.max_parallel // launching_arms))

    def peak_parallel(self, launching_arms: int) -> int:
        """Peak total concurrency given the arms launching at once."""
        arms = max(launching_arms, 1)
        return self.effective_per_variant(arms) * arms


MAX_VARIANTS = 16


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    name: str
    model: ModelSpec = Field(default_factory=ModelSpec)
    thinking: ThinkingLevel = "high"
    pi_version: str = DEFAULT_PI_VERSION
    pier_version: str = ">=0.3,<0.4"
    tasks: TaskSelection
    control: ControlSpec | None = None
    variants: list[VariantSpec] = Field(default_factory=list)
    concurrency: ConcurrencySpec = Field(default_factory=ConcurrencySpec)

    @field_validator("pi_version")
    @classmethod
    def _safe_pi_version(cls, value: str) -> str:
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", value):
            raise ValueError(f"pi_version must be an exact npm version, got {value!r}")
        return value

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value}, expected 1")
        return value

    @model_validator(mode="after")
    def _require_arms_and_unique_ids(self) -> ExperimentSpec:
        has_control = self.control is not None and self.control.enabled
        if not self.variants and not has_control:
            raise ValueError("experiment needs at least one variant or an "
                             "enabled control arm")
        ids = [v.id for v in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate variant ids: {sorted(ids)}")
        reserved = sorted(set(ids) & RESERVED_VARIANT_IDS)
        if reserved:
            raise ValueError(
                f"variant ids reserved for the control arm: {reserved}"
            )
        return self

    @field_validator("variants")
    @classmethod
    def _cap_variants(cls, value: list[VariantSpec]) -> list[VariantSpec]:
        if len(value) > MAX_VARIANTS:
            raise ValueError(
                f"experiment allows at most {MAX_VARIANTS} variants, "
                f"got {len(value)}"
            )
        return value

    def arms(self) -> list[VariantSpec]:
        """Every launched arm. The control is a bare VariantSpec named control."""
        arms: list[VariantSpec] = []
        if self.control is not None and self.control.enabled:
            arms.append(VariantSpec(id="control", name="Bare Pi control"))
        arms.extend(self.variants)
        return arms

    def peak_concurrency(self) -> int:
        """Peak total trials in flight when all arms launch at once."""
        return self.concurrency.peak_parallel(len(self.arms()))
