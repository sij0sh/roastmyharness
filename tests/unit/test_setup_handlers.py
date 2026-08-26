"""Setup handler inputs cannot escape their intended shell arguments."""

from __future__ import annotations

from roast_my_harness.adapter.setup_handlers import (
    _safe_command_path,
    _safe_npm_pin,
    _safe_remote_directory,
)


def test_safe_npm_pin_accepts_scoped_exact_versions():
    assert _safe_npm_pin("@scope/package@1.2.3")
    assert _safe_npm_pin("package@1.2.3-beta.1")
    assert not _safe_npm_pin("package@latest")
    assert not _safe_npm_pin("package@1.2.3; echo leaked")


def test_safe_install_paths_reject_traversal_and_shell_syntax():
    assert _safe_remote_directory("/usr/local/bin")
    assert not _safe_remote_directory("/usr/local/../bin")
    assert _safe_command_path("/usr/local/bin/tool")
    assert not _safe_command_path("tool; echo leaked")
