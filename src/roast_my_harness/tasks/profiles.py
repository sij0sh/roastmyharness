"""Versioned model profiles: measured benchmark rates per model.

Profiles live beside the benchmark catalog and record only measured
values; unmeasured fields stay null and the wizard shows no rate it
cannot cite. Ranking scores expected pass rates against the 40-60%
discrimination band: in-band profiles first, then by distance, then
unmeasured profiles in file order. Pi-inventory filtering happens
caller-side (the wizard owns the inventory); rank() only flags matches.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from roast_my_harness.errors import SpecError

PROFILES_FILENAME = "profiles.toml"
PROFILES_VERSION = 1

BAND_LO = 0.4
BAND_HI = 0.6


@dataclass(frozen=True)
class ModelProfile:
    id: str
    label: str
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    benchmark_rate: float | None = None
    benchmark_samples: int | None = None
    benchmark_revision: str | None = None
    benchmark_date: str | None = None
    basis: str = ""

    def full_id(self) -> str | None:
        if self.provider and self.model:
            return f"{self.provider}/{self.model}"
        return None


@dataclass(frozen=True)
class BenchmarkProfiles:
    path: Path
    benchmark: str
    revision: str
    profiles: tuple[ModelProfile, ...] = ()


@dataclass(frozen=True)
class RankedProfile:
    profile: ModelProfile
    expected_rate: float | None
    distance: float | None
    matched: bool | None


def _parse_profile(raw: object) -> ModelProfile:
    if not isinstance(raw, dict):
        raise SpecError("model profile must be a mapping")
    profile_id = raw.get("id")
    label = raw.get("label")
    if not profile_id or not label:
        raise SpecError("model profile needs id and label")
    rate = raw.get("benchmark_rate")
    if rate is not None and not isinstance(rate, (int, float)):
        raise SpecError(f"profile {profile_id!r} benchmark_rate must be a number")
    if rate is not None and not 0.0 <= float(rate) <= 1.0:
        raise SpecError(f"profile {profile_id!r} benchmark_rate must be in [0, 1]")
    samples = raw.get("benchmark_samples")
    if samples is not None and (not isinstance(samples, int) or samples < 1):
        raise SpecError(f"profile {profile_id!r} benchmark_samples must be >= 1")
    if rate is None and samples is not None:
        raise SpecError(f"profile {profile_id!r} has samples without a rate")
    return ModelProfile(
        id=str(profile_id),
        label=str(label),
        provider=raw.get("provider"),
        model=raw.get("model"),
        thinking=raw.get("thinking"),
        benchmark_rate=float(rate) if rate is not None else None,
        benchmark_samples=samples,
        benchmark_revision=raw.get("benchmark_revision"),
        benchmark_date=raw.get("benchmark_date"),
        basis=str(raw.get("basis", "")),
    )


def load_profiles(root: Path) -> BenchmarkProfiles | None:
    """Parse root/profiles.toml; None when the benchmark has no profiles."""
    path = Path(root) / PROFILES_FILENAME
    if not path.is_file():
        return None
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SpecError(f"cannot read model profiles {path}: {e}") from e
    if not isinstance(raw, dict):
        raise SpecError(f"invalid model profiles {path}: expected a mapping")
    if raw.get("profiles_version", 1) != PROFILES_VERSION:
        raise SpecError(
            f"unsupported profiles_version {raw.get('profiles_version')} in {path}, "
            f"expected {PROFILES_VERSION}"
        )
    entries = raw.get("profiles", [])
    if not isinstance(entries, list):
        raise SpecError(f"model profiles {path} needs a profiles list")
    profiles = tuple(_parse_profile(entry) for entry in entries)
    ids = [p.id for p in profiles]
    if len(set(ids)) != len(ids):
        raise SpecError(f"duplicate profile ids in {path}")
    return BenchmarkProfiles(
        path=path,
        benchmark=str(raw.get("benchmark", path.parent.name)),
        revision=str(raw.get("benchmark_revision", "")),
        profiles=profiles,
    )


def band_distance(rate: float) -> float:
    """0 inside the discrimination band, else distance to its nearer edge."""
    if BAND_LO <= rate <= BAND_HI:
        return 0.0
    return min(abs(rate - BAND_LO), abs(rate - BAND_HI))


def rank_profiles(
    profiles: BenchmarkProfiles,
    *,
    task_ids: list[str] | None = None,
    inventory: list[str] | None = None,
) -> list[RankedProfile]:
    """Rank profiles for a task set, optionally flagging inventory matches.

    task_ids is accepted for per-task calibration weighting once measured
    per-task rates land; v1 ranks by overall benchmark rate. inventory is
    the user's Pi model ids ("provider/model"); matched flags which
    profiles are runnable, without filtering (the caller decides display).
    """
    del task_ids  # reserved for per-task calibration weighting
    known = set(inventory or [])
    ranked: list[RankedProfile] = []
    for profile in profiles.profiles:
        rate = profile.benchmark_rate
        ranked.append(
            RankedProfile(
                profile=profile,
                expected_rate=rate,
                distance=band_distance(rate) if rate is not None else None,
                matched=profile.full_id() in known if inventory is not None else None,
            )
        )
    ranked.sort(
        key=lambda r: (
            r.distance is None,
            r.distance if r.distance is not None else 0.0,
            -(r.profile.benchmark_samples or 0),
        )
    )
    return ranked


@dataclass(frozen=True)
class ProfileView:
    """JSON-ready profile row for the wizard and CLI."""

    id: str
    label: str
    full_id: str | None
    thinking: str | None
    expected_rate: float | None
    distance: float | None
    matched: bool | None
    samples: int | None
    revision: str | None
    basis: str = field(default="")
