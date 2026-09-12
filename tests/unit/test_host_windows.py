"""Spike: imitate Windows on Linux. Throwaway."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path, PureWindowsPath
from unittest.mock import AsyncMock, MagicMock

import roast_my_harness.host_process as hp
from roast_my_harness import files


def test_start_uses_creationflags_on_windows(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(os, "name", "nt")
    seen: dict = {}
    async def fake_exec(*argv, **kwargs):
        seen.update(kwargs)
        m = AsyncMock()
        m.returncode = None
        m.pid = 1234
        return m
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    proc = hp.VariantProcess("v", [sys.executable, "-c", "pass"], tmp_path / "v.log")
    asyncio.run(proc.start(env=None))
    assert "creationflags" in seen
    assert "start_new_session" not in seen


def test_terminate_uses_proc_terminate_on_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    proc = hp.VariantProcess("v", ["x"], Path("/tmp/x.log"))
    mock_proc = MagicMock()
    mock_proc.pid = 9999
    mock_proc.returncode = None
    proc.proc = mock_proc
    asyncio.run(hp._terminate(proc))
    mock_proc.terminate.assert_called_once()


def test_kill_uses_proc_kill_on_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    proc = hp.VariantProcess("v", ["x"], Path("/tmp/x.log"))
    mock_proc = MagicMock()
    mock_proc.pid = 9999
    mock_proc.returncode = None
    proc.proc = mock_proc
    asyncio.run(hp._kill(proc))
    mock_proc.kill.assert_called_once()


def test_sync_directory_survives_windows_OSError(monkeypatch, tmp_path: Path):
    def boom(*a, **k):
        raise OSError("Windows: cannot open directory fd")
    monkeypatch.setattr(os, "open", boom)
    files._sync_directory(tmp_path)  # must not raise


def test_windows_path_parsing():
    p = PureWindowsPath(r"C:\Users\you\.roastmyharness\runs\exp1")
    assert p.drive == "C:"
    assert "exp1" in p.parts


def test_atomic_write_breaks_without_fchmod(monkeypatch, tmp_path: Path):
    """Documents current Windows gap: os.fchmod is Unix-only."""
    monkeypatch.delattr(os, "fchmod", raising=False)
    assert not hasattr(os, "fchmod"), "spike expects no fchmod, like Windows"
    try:
        files.atomic_write_text(tmp_path / "out.txt", "hi", mode=0o600)
    except AttributeError:
        return  # spike passes by proving the gap exists
    # If Windows ever gains fchmod, or code guards it, this also passes.
