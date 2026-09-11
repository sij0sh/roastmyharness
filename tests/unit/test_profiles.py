"""Model profiles: measured rates only, ranked toward the 40-60% band."""

from __future__ import annotations

from pathlib import Path

import pytest

from roast_my_harness.errors import SpecError
from roast_my_harness.tasks.profiles import band_distance, load_profiles, rank_profiles

PROFILES = """
profiles_version = 1
benchmark = "mini"
benchmark_revision = "2026-09"

[[profiles]]
id = "mid"
label = "Mid"
provider = "p"
model = "m"
thinking = "high"
benchmark_rate = 0.5
benchmark_samples = 100
benchmark_revision = "pub"
basis = "test"

[[profiles]]
id = "hot"
label = "Hot"
provider = "p"
model = "h"
benchmark_rate = 0.9
benchmark_samples = 100
benchmark_revision = "pub"
basis = "test"

[[profiles]]
id = "new"
label = "New"
basis = "unmeasured"
"""


def write_profiles(root: Path, text: str = PROFILES) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "profiles.toml"
    path.write_text(text)
    return root


def test_load_profiles_parses_measured_and_unmeasured(tmp_path: Path):
    found = load_profiles(write_profiles(tmp_path / "bench"))
    assert found is not None
    assert [p.id for p in found.profiles] == ["mid", "hot", "new"]
    mid = found.profiles[0]
    assert mid.full_id() == "p/m"
    assert mid.benchmark_rate == 0.5
    new = found.profiles[2]
    assert new.full_id() is None
    assert new.benchmark_rate is None


def test_load_profiles_absent_returns_none(tmp_path: Path):
    assert load_profiles(tmp_path) is None


def test_load_profiles_rejects_bad_data(tmp_path: Path):
    root = write_profiles(tmp_path / "bench")
    path = root / "profiles.toml"
    original = path.read_text()
    path.write_text(original.replace("benchmark_rate = 0.5", "benchmark_rate = 1.5"))
    with pytest.raises(SpecError, match="in \\[0, 1\\]"):
        load_profiles(root)
    path.write_text(original + '\n[[profiles]]\nid = "mid"\nlabel = "Dup"\n')
    with pytest.raises(SpecError, match="duplicate profile ids"):
        load_profiles(root)
    path.write_text("profiles_version = 99\n")
    with pytest.raises(SpecError, match="profiles_version"):
        load_profiles(root)


def test_band_distance():
    assert band_distance(0.4) == 0.0
    assert band_distance(0.5) == 0.0
    assert band_distance(0.6) == 0.0
    assert band_distance(0.634) == pytest.approx(0.034)
    assert band_distance(0.1) == pytest.approx(0.3)


def shipped():
    root = Path(__file__).resolve().parents[2] / "tasks" / "deepswe" / "tasks"
    found = load_profiles(root)
    assert found is not None
    return found


def test_shipped_profiles_cite_measured_rates():
    found = shipped()
    by_id = {p.id: p for p in found.profiles}
    assert set(by_id) == {"luna-high", "glm-flash-max", "muse-spark", "sol"}
    luna = by_id["luna-high"]
    assert luna.full_id() == "openai-codex/gpt-5.6-luna"
    assert luna.benchmark_rate == 0.442
    assert luna.benchmark_samples == 452
    assert "200/452" in luna.basis
    glm = by_id["glm-flash-max"]
    assert glm.full_id() == "z-ai-openai/glm-5.3-flash"
    assert glm.benchmark_rate == 0.634
    assert glm.benchmark_samples == 448
    assert "284/448" in glm.basis
    for profile_id in ("muse-spark", "sol"):
        unmeasured = by_id[profile_id]
        assert unmeasured.benchmark_rate is None
        assert unmeasured.full_id() is None
        assert unmeasured.basis


def test_rank_prefers_band_then_distance_then_unmeasured():
    ranked = rank_profiles(shipped())
    assert [r.profile.id for r in ranked] == [
        "luna-high", "glm-flash-max", "muse-spark", "sol",
    ]
    assert ranked[0].distance == 0.0
    assert ranked[1].distance == pytest.approx(0.034)
    assert ranked[2].distance is None


def test_rank_flags_inventory_matches():
    ranked = rank_profiles(
        shipped(), inventory=["z-ai-openai/glm-5.3-flash", "other/model"]
    )
    matched = {r.profile.id: r.matched for r in ranked}
    assert matched == {
        "luna-high": False,
        "glm-flash-max": True,
        "muse-spark": False,
        "sol": False,
    }


def test_profiles_rank_measured_first():
    root = Path(__file__).resolve().parents[2] / "tasks" / "deepswe" / "tasks"
    found = load_profiles(root)
    assert found is not None and found.benchmark == "deepswe"
    ranked = rank_profiles(found)
    first, *_, last = ranked
    assert first.profile.id == "luna-high"
    assert first.expected_rate == 0.442
    assert last.expected_rate is None


def test_profiles_missing_dir_returns_none(tmp_path: Path):
    assert load_profiles(tmp_path) is None
