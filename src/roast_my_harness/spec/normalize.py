"""Path and id normalization shared by loader, builder, and CLI."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def absolute(path: Path, base_dir: Path) -> Path:
    """Expand user paths and resolve relative paths against the spec file."""
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = base_dir / expanded
    return expanded.resolve()


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "experiment"


def experiment_id(name: str, spec_hash: str) -> str:
    return f"{slugify(name)}-{spec_hash[:8]}"
