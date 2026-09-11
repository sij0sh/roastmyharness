"""Pydantic v2 schema for TOML experiment files. Pi-only, schema v3."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from roast_my_harness.adapter.versions import LATEST, resolve_package_version, validate_agent_pin
from roast_my_harness.constants import DEFAULT_PI_VERSION, FAIRNESS_FLAGS
from roast_my_harness.evals.registry import BUNDLED_EVAL_IDS
from roast_my_harness.observability import SECRET_KEY_WORDS

SCHEMA_VERSION = 3
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
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise ValueError(f"{field} must be slug-safe, got {value!r}")
    return value


def _safe_rel_path(value: str, field: str) -> str:
    if value.startswith(("/", "\\")) or Path(value).is_absolute():
        raise ValueError(f"{field} must be relative, got {value!r}")
    parts = value.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{field} must not contain '..'/'.': {value!r}")
    return value


def _pi_pin(value: str, field: str) -> str:
    return validate_agent_pin(value, field)


def _safe_env_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"environment variable name {value!r} must be UPPER_SNAKE_CASE")
    return value


ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]


class ResolvedModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    provider_block_sha256: str
    env_vars: list[str] = Field(default_factory=list)


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = "gpt-5.6-luna"
    provider: str = "openai-codex"
    resolved_model: ResolvedModelSpec | None = None

    def full_id(self) -> str:
        return f"{self.provider}/{self.id}"

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            if "/" not in value:
                raise ValueError(f"model must be 'provider/model', got {value!r}")
            provider, model_id = value.split("/", 1)
            return {"provider": provider, "id": model_id}
        return value


class LocalExtension(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["local"] = "local"
    path: Path
    entry: str
    name: str | None = None
    exclude: list[str] = Field(default_factory=list)

    @field_validator("entry")
    @classmethod
    def _safe_entry(cls, value: str) -> str:
        return _safe_rel_path(value, "extension entry")

    @field_validator("name")
    @classmethod
    def _safe_name(cls, value: str | None) -> str | None:
        return _safe_relative_component(value, "extension name") if value else value


class NpmExtension(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["npm"] = "npm"
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
        if not (sep and re.fullmatch(r"@?[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)?", name)
                and re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.-]+)*", version)):
            raise ValueError(f"npm package must pin exact version, got {value!r}")
        return value


def _infer_extension(value: Any) -> Any:
    if isinstance(value, dict) and "kind" not in value:
        if "package" in value:
            return {**value, "kind": "npm"}
        if "path" in value:
            return {**value, "kind": "local"}
    return value


ExtensionSpec = Annotated[LocalExtension | NpmExtension, Field(discriminator="kind")]


class SkillSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["local"] = "local"
    path: Path
    name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, value: Any) -> Any:
        return {"path": value} if isinstance(value, str) else value

    @field_validator("name")
    @classmethod
    def _safe_name(cls, value: str | None) -> str | None:
        return _safe_relative_component(value, "skill name") if value else value


class VariantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str | None = None
    pi_version: str | None = None
    extensions: list[ExtensionSpec] = Field(default_factory=list)
    skills: list[SkillSpec] = Field(default_factory=list)
    agents_md: Path | None = None
    settings: Path | None = None
    env: dict[str, str] = Field(default_factory=dict)
    env_from_host: list[str] = Field(default_factory=list)
    egress_urls: list[str] = Field(default_factory=list)
    pi_flags: list[str] = Field(default_factory=list)

    @field_validator("extensions", mode="before")
    @classmethod
    def _infer_ext_kinds(cls, value: Any) -> Any:
        return [_infer_extension(v) for v in value] if isinstance(value, list) else value

    @field_validator("id")
    @classmethod
    def _safe_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError(f"variant id {value!r} must be lowercase alphanumeric/hyphen")
        return value

    @field_validator("pi_version")
    @classmethod
    def _safe_pin(cls, value: str | None) -> str | None:
        return None if value is None else _pi_pin(value, "variants.pi_version")

    @field_validator("env")
    @classmethod
    def _no_secret_literals(cls, value: dict[str, str]) -> dict[str, str]:
        from roast_my_harness.observability import contains_secret
        for key, item in value.items():
            _safe_env_name(key)
            lowered = key.lower().replace("-", "_")
            if any(word in lowered for word in SECRET_KEY_WORDS):
                raise ValueError(f"env key {key!r} looks like a credential; use env_from_host")
            if contains_secret(item):
                raise ValueError(f"env value for {key!r} looks like a credential; use env_from_host")
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
                raise ValueError(f"pi_flags entries must be single tokens, got {flag!r}")
            name = flag.split("=", 1)[0]
            if name in RESERVED_PI_FLAGS:
                raise ValueError(f"pi_flags entry {flag!r} conflicts with harness-controlled flags")
            if name not in ALLOWED_PI_FLAGS:
                raise ValueError(f"pi_flags entry {flag!r} not allowlisted: {sorted(ALLOWED_PI_FLAGS)}")
        return value

class TaskSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Path
    include: list[str] = Field(default_factory=lambda: ["*"])
    exclude: list[str] = Field(default_factory=list)
    preset: str | None = None


class EvalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bundled", "external"] = "bundled"
    id: str | None = None
    revision: str | None = None

    @field_validator("id")
    @classmethod
    def _safe_id(cls, value: str | None) -> str | None:
        return _safe_relative_component(value, "evaluation id") if value else value

    @model_validator(mode="after")
    def _require_known(self) -> EvalSpec:
        eval_id = self.id or "deepswe"
        if self.type == "bundled" and eval_id not in BUNDLED_EVAL_IDS:
            raise ValueError(f"unknown bundled eval {eval_id!r}")
        if self.type != "bundled" and not self.id:
            raise ValueError(f"evaluation.id is required for type {self.type!r}")
        return self


class ConcurrencySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    per_variant: int = Field(default=2, ge=1, le=16)
    max_parallel: int | None = Field(default=None, ge=1, le=32)
    launch_max_in_flight: int = Field(default=8, ge=1, le=32)
    launch_stagger_sec: float = Field(default=0.5, ge=0.0, le=10.0)
    quota_max_parallel: int | None = Field(default=None, ge=1, le=32)

    def effective_per_variant(self, launching_arms: int) -> int:
        per = self.per_variant
        if launching_arms >= 1 and self.max_parallel is not None:
            per = max(1, min(per, self.max_parallel // launching_arms))
        if launching_arms >= 1 and self.quota_max_parallel is not None:
            per = max(1, min(per, self.quota_max_parallel // launching_arms))
        return per

    def peak_parallel(self, launching_arms: int) -> int:
        return self.effective_per_variant(max(launching_arms, 1)) * max(launching_arms, 1)


MAX_VARIANTS = 16
MAX_REPETITIONS = 16


class ExecutionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repetitions: int = Field(default=1, ge=1, le=MAX_REPETITIONS)
    max_retries: int = Field(default=1, ge=0, le=MAX_REPETITIONS)


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = SCHEMA_VERSION
    name: str
    hypothesis: str = Field(default="")
    model: ModelSpec = Field(default_factory=ModelSpec)
    thinking: ThinkingLevel = "high"
    execution: ExecutionSpec = Field(default_factory=ExecutionSpec)
    pi_version: str = DEFAULT_PI_VERSION
    pier_version: str = ">=0.3,<0.4"
    tasks: TaskSelection
    evaluation: EvalSpec | None = None
    variants: list[VariantSpec] = Field(default_factory=list)
    concurrency: ConcurrencySpec = Field(default_factory=ConcurrencySpec)
    control: bool = True

    @field_validator("model", mode="before")
    @classmethod
    def _coerce_model(cls, value: Any) -> Any:
        return ModelSpec.model_validate(value) if isinstance(value, str) else value

    @field_validator("pi_version")
    @classmethod
    def _safe_pi(cls, value: str) -> str:
        return _pi_pin(value, "pi_version")

    @field_validator("schema_version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value in (1, 2):
            raise ValueError(f"schema_version {value} no longer accepted: set schema_version = 3")
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value}, expected {SCHEMA_VERSION}")
        return value

    @model_validator(mode="after")
    def _require_arms(self) -> ExperimentSpec:
        if not self.variants:
            raise ValueError("experiment needs at least one variant")
        ids = [v.id for v in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate variant ids: {sorted(ids)}")
        reserved = sorted(set(ids) & RESERVED_VARIANT_IDS)
        if reserved:
            raise ValueError(f"variant ids reserved for the control arm: {reserved}")
        return self

    @field_validator("variants")
    @classmethod
    def _cap(cls, value: list[VariantSpec]) -> list[VariantSpec]:
        if len(value) > MAX_VARIANTS:
            raise ValueError(f"at most {MAX_VARIANTS} variants, got {len(value)}")
        return value

    def pi_version_for(self, variant: VariantSpec | None = None) -> str:
        if variant is not None and variant.pi_version is not None:
            return variant.pi_version
        return self.pi_version

    def resolved_pi_version_for(self, variant: VariantSpec | None = None) -> str:
        from roast_my_harness.adapter.registry import PI_NPM_PACKAGE
        pin = self.pi_version_for(variant)
        if pin == LATEST:
            return resolve_package_version(PI_NPM_PACKAGE, pin)
        return pin

    def arms(self) -> list[VariantSpec]:
        bare = [VariantSpec(id="control", name="Bare control")] if self.control else []
        return [*bare, *self.variants]

    def peak_concurrency(self) -> int:
        return self.concurrency.peak_parallel(len(self.arms()) * max(self.execution.repetitions, 1))
