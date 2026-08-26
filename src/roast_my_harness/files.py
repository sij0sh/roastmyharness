"""Small filesystem helpers for durable, permission-aware writes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """Replace a text file atomically within its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    open_fd: int | None = fd
    try:
        if mode is not None:
            try:
                os.fchmod(fd, mode)
            except AttributeError:
                os.chmod(temporary, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            open_fd = None
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if mode is not None:
            os.chmod(path, mode)
        _sync_directory(path.parent)
    finally:
        if open_fd is not None:
            os.close(open_fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _sync_directory(directory: Path) -> None:
    """Best-effort directory sync after an atomic replacement."""
    try:
        directory_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)
