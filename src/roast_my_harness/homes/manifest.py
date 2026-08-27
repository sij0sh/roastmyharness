"""variant.json: the contract between the host builder and the adapter."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ManifestExtension(BaseModel):
    name: str
    entry: str  # path relative to the home dir, e.g. extensions/foo/src/index.ts
    source_hash: str = ""


class ManifestSkill(BaseModel):
    name: str
    path: str  # path relative to the home dir, e.g. skills/foo


class ManifestSetupStep(BaseModel):
    handler: str
    args: dict[str, str] = Field(default_factory=dict)


class VariantManifest(BaseModel):
    """Written as variant.json into each built home; the adapter's only input."""

    schema_version: int = 1
    variant_id: str
    variant_hash: str
    pi_version: str
    model_id: str  # complete provider/model string
    extensions: list[ManifestExtension] = Field(default_factory=list)
    skills: list[ManifestSkill] = Field(default_factory=list)
    npm_packages: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_from_host: list[str] = Field(default_factory=list)
    setup: list[ManifestSetupStep] = Field(default_factory=list)
    egress_urls: list[str] = Field(default_factory=list)
    pi_flags: list[str] = Field(default_factory=list)
