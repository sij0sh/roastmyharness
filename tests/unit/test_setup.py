"""Pi-only setup with copy install."""

from __future__ import annotations

from pathlib import Path

from roast_my_harness import setup as setup_mod


def _make_repo(root: Path) -> Path:
    ext = root / "integrations/pi/roastmyharness.ts"
    ext.parent.mkdir(parents=True, exist_ok=True)
    ext.write_text("export default {};\n")
    modules = root / "integrations/pi/roastmyharness"
    modules.mkdir(parents=True, exist_ok=True)
    (modules / "extension.ts").write_text("export default {};\n")
    return root


def test_setup_pi_user_is_idempotent(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    for _ in range(2):
        results = setup_mod.setup("pi", "user", root=root, home=home)
    assert not [r for r in results if r.problem]
    assert (home / ".pi/agent/extensions/roastmyharness.ts").is_file()
    assert (home / ".pi/agent/extensions/roastmyharness/extension.ts").is_file()
    assert not [r for r in results if r.problem]


def test_setup_replaces_legacy_symlinks_with_copies(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    dest_dir = home / ".pi/agent/extensions"
    dest_dir.mkdir(parents=True)
    (dest_dir / "roastmyharness.ts").symlink_to(root / "integrations/pi/roastmyharness.ts")
    (dest_dir / "roastmyharness").symlink_to(root / "integrations/pi/roastmyharness")
    results = setup_mod.setup("pi", "user", root=root, home=home)
    assert not [r for r in results if r.problem]
    installed_file = dest_dir / "roastmyharness.ts"
    installed_dir = dest_dir / "roastmyharness"
    assert installed_file.is_file() and not installed_file.is_symlink()
    assert installed_dir.is_dir() and not installed_dir.is_symlink()
    assert (installed_dir / "extension.ts").is_file()


def test_setup_rejects_non_pi_and_bad_scope(tmp_path: Path) -> None:
    root = _make_repo(tmp_path / "repo")
    assert setup_mod.setup("claude", "user", root=root)[0].problem
    assert setup_mod.setup("cursor", "user", root=root)[0].problem
    assert setup_mod.setup("pi", "global", root=root)[0].problem


def test_doctor_reports_health(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    results = setup_mod.run_doctor(root=root, home=home)
    names = {r.name for r in results}
    assert {"python", "pier", "docker", "auth", "model"} <= names
    assert "mcp" not in names
