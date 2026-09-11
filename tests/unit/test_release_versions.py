"""Release versions stay in sync across Python, npm, and the extension."""

from __future__ import annotations

import json
import re
from pathlib import Path

from roast_my_harness import __version__


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for base in [Path.cwd(), *here.parents]:
        if (base / "package.json").is_file() and (base / "pyproject.toml").is_file():
            return base
    raise AssertionError("repo root not found")


def test_versions_in_sync() -> None:
    root = _repo_root()
    pkg = json.loads((root / "package.json").read_text())
    assert pkg["version"] == __version__
    text = (root / "integrations/pi/roastmyharness/versions.ts").read_text()
    match = re.search(r'EXPECTED_ENGINE_VERSION\s*=\s*"([^"]+)"', text)
    assert match is not None
    assert match.group(1) == __version__


def test_pi_manifest_points_at_extension() -> None:
    root = _repo_root()
    pkg = json.loads((root / "package.json").read_text())
    assert "pi-package" in pkg.get("keywords", [])
    assert "./integrations/pi/roastmyharness.ts" in pkg["pi"]["extensions"]
