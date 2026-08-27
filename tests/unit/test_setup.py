"""Idempotent setup and doctor behavior (hermetic: tmp roots and homes)."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness import setup as setup_mod


def _make_repo(root: Path) -> Path:
    skill = root / ".agents/skills/roast-my-harness"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# roast-my-harness\n")
    ext = root / "integrations/pi/roast-my-harness.ts"
    ext.parent.mkdir(parents=True)
    ext.write_text("export default {};\n")
    return root


def test_setup_pi_user_is_idempotent(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    for _ in range(2):
        results = setup_mod.setup("pi", "user", root=root, home=home)
    assert not [r for r in results if r.problem]
    link = home / ".pi/agent/skills/roast-my-harness"
    assert (
        link.is_symlink() and link.resolve() == (root / ".agents/skills/roast-my-harness").resolve()
    )
    assert (home / ".pi/agent/extensions/roast-my-harness.ts").is_symlink()
    assert not any(r.changed for r in results)


def test_setup_preserves_existing_real_paths(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    conflict = home / ".pi/agent/extensions/roast-my-harness.ts"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("real file\n")
    results = setup_mod.setup("pi", "user", root=root, home=home)
    problems = [r for r in results if r.problem]
    assert len(problems) == 1
    assert conflict.read_text() == "real file\n"


def test_setup_replaces_stale_symlink(tmp_path: Path) -> None:
    root, home = _make_repo(tmp_path / "repo"), tmp_path / "home"
    stale = home / ".claude/skills/roast-my-harness"
    stale.parent.mkdir(parents=True)
    stale.symlink_to(tmp_path / "elsewhere")
    results = setup_mod.setup("claude", "user", root=root, home=home)
    assert not [r for r in results if r.problem]
    assert stale.resolve() == (root / ".agents/skills/roast-my-harness").resolve()


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
