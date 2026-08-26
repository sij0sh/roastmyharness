"""Pydantic v2 experiment schema. One TOML file defines a whole experiment."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = 1
RESERVED_VARIANT_IDS = {"control"}

# Filesystem-safe: lowercase alnum plus hyphen, must start alphanumeric.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]


class ResolvedModelSpec(BaseModel):
    """Host-config materialization: what actually runs, recorded at load
    time so spec_hash covers host configuration drift."""

    provider: str
    provider_block_sha256: str
    env_vars: list[str] = Field(default_factory=list)


class ModelSpec(BaseModel):
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
    kind: Literal["local"]
    path: Path
    entry: str
    name: str | None = None
    exclude: list[str] = Field(default_factory=list)
    runtime_packages: list[str] = Field(default_factory=list)


class NpmExtension(BaseModel):
    kind: Literal["npm"]
    package: str
    name: str | None = None

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
    kind: Literal["local"] = "local"
    path: Path
    name: str | None = None


class NpmPiInstall(BaseModel):
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
    handler: Literal["install_binary"]
    source: Path
    destination: str = "/usr/local/bin"
    verify: str | None = None


class RunRtkInit(BaseModel):
    handler: Literal["run_rtk_init"]


class CodegraphIndex(BaseModel):
    handler: Literal["codegraph_index"]
    bundle: Path


SetupSpec = Annotated[
    NpmPiInstall | InstallBinary | RunRtkInit | CodegraphIndex,
    Field(discriminator="handler"),
]


class VariantSpec(BaseModel):
    id: str
    name: str | None = None
    extensions: list[ExtensionSpec] = Field(default_factory=list)
    skills: list[SkillSpec] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
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


class TaskSelection(BaseModel):
    path: Path
    include: list[str] = Field(default_factory=lambda: ["*"])
    exclude: list[str] = Field(default_factory=list)


class ControlSpec(BaseModel):
    enabled: bool = True
    reuse: Literal["never", "ask", "require"] = "ask"
    minimum_runs_per_task: int = 10
    maximum_age_days: int = 30
    sentinel_tasks: int = 6  

    @field_validator("sentinel_tasks")
    @classmethod
    def _sentinels(cls, value: int) -> int:
        if value < 0:
            raise ValueError("sentinel_tasks must be >= 0")
        return value


class ConcurrencySpec(BaseModel):
    per_variant: int = 2


class ExperimentSpec(BaseModel):
    schema_version: int = SCHEMA_VERSION
    name: str
    model: ModelSpec = Field(default_factory=ModelSpec)
    thinking: ThinkingLevel = "high"
    pi_version: str = "0.84.3"
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

    def arms(self) -> list[VariantSpec]:
        """Every launched arm. The control is a bare VariantSpec named control."""
        arms: list[VariantSpec] = []
        if self.control is not None and self.control.enabled:
            arms.append(VariantSpec(id="control", name="Bare Pi control"))
        arms.extend(self.variants)
        return arms
