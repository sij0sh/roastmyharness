"""Idempotent setup and doctor behavior (hermetic: tmp roots and homes)."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness import setup as setup_mod


def _make_repo(root: Path) -> Path:
    ext = root / "integrations/pi/roastmyharness.ts"
    ext.parent.mkdir(parents=True)
    ext.write_text("export default {};\n")
    modules = root / "integrations/pi/roastmyharness"
    modules.mkdir()
    (modules / "extension.ts").write_text("export default {};\n")
    return root


def test_setup_pi_user_is_idempotent(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    for _ in range(2):
        results = setup_mod.setup("pi", "user", root=root, home=home)
    assert not [r for r in results if r.problem]
    assert (home / ".pi/agent/extensions/roastmyharness.ts").is_symlink()
    assert (home / ".pi/agent/extensions/roastmyharness").is_symlink()
    assert not (home / ".pi/agent/skills/roastmyharness").exists()
    assert not any(r.changed for r in results)


def test_setup_preserves_existing_real_paths(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    conflict = home / ".pi/agent/extensions/roastmyharness.ts"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("real file\n")
    results = setup_mod.setup("pi", "user", root=root, home=home)
    problems = [r for r in results if r.problem]
    assert len(problems) == 1
    assert conflict.read_text() == "real file\n"


def test_setup_replaces_stale_symlink(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    stale = home / ".pi/agent/extensions/roastmyharness.ts"
    stale.parent.mkdir(parents=True)
    stale.symlink_to(tmp_path / "elsewhere")
    results = setup_mod.setup("pi", "user", root=root, home=home)
    assert not [r for r in results if r.problem]
    assert stale.resolve() == (root / "integrations/pi/roastmyharness.ts").resolve()
    modules = home / ".pi/agent/extensions/roastmyharness"
    assert modules.resolve() == (root / "integrations/pi/roastmyharness").resolve()


def test_setup_removes_obsolete_skill_symlink(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    stale = home / ".pi/agent/skills/roastmyharness"
    stale.parent.mkdir(parents=True)
    stale.symlink_to(tmp_path / "removed-skill")
    results = setup_mod.setup("pi", "user", root=root, home=home)
    assert not [result for result in results if result.problem]
    assert not stale.is_symlink()
    assert any(result.changed and "obsolete skill" in result.name for result in results)


def test_setup_preserves_real_obsolete_skill_path(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    skill = home / ".pi/agent/skills/roastmyharness"
    skill.mkdir(parents=True)
    results = setup_mod.setup("pi", "user", root=root, home=home)
    assert any(result.problem and "remove it manually" in result.detail for result in results)
    assert skill.is_dir()


def test_setup_claude_merges_and_preserves_config(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    home.mkdir()
    config = home / ".claude.json"
    config.write_text(
        json.dumps(
            {
                "otherKey": 1,
                "mcpServers": {"someone-else": {"command": "x"}},
            }
        )
    )
    setup_mod.setup("claude", "user", root=root, home=home)
    setup_mod.setup("claude", "user", root=root, home=home)
    data = json.loads(config.read_text())
    assert data["otherKey"] == 1
    assert data["mcpServers"]["someone-else"] == {"command": "x"}
    ours = data["mcpServers"][setup_mod.MCP_SERVER_NAME]
    assert ours["args"] == ["-m", "roast_my_harness.mcp_server"]
    assert not (home / ".claude/skills/roastmyharness").exists()


def test_setup_claude_project_scope_writes_mcp_json(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    setup_mod.setup("claude", "project", root=root, home=home)
    data = json.loads((root / ".mcp.json").read_text())
    assert setup_mod.MCP_SERVER_NAME in data["mcpServers"]


def test_setup_rejects_unknown_agent_and_scope(tmp_path: Path) -> None:
    root = _make_repo(tmp_path / "repo")
    assert setup_mod.setup("cursor", "user", root=root)[0].problem
    assert setup_mod.setup("pi", "global", root=root)[0].problem


def test_doctor_reports_health(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    results = setup_mod.run_doctor(root=root, home=home)
    names = {r.name for r in results}
    assert {"python", "pi", "pier", "docker", "auth", "model"} <= names
