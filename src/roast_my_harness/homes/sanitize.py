"""Common source sanitizer applied to every copied extension and skill.

Excludes dev caches, tests, build output, instruction files, and known
secret files. Never excludes *.md blanket-style: SKILL.md and extension
docs must survive.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

EXCLUDED_DIR_NAMES = {
    ".git",
    ".pi-files",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "htmlcov",
    "test",
    "tests",
}

EXCLUDED_FILE_PATTERNS = [
    ".DS_Store",
    "*.pyc",
    "*.test.ts",
    "*.test.js",
    "*.test.mjs",
    "tsconfig.json",
    "package-lock.json",
    # Instruction files would leak repo context into every arm.
    "AGENTS.md",
    "CLAUDE.md",
    # Known secret files; never stage these.
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
]

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")


def _match_pattern(rel_posix: str, name: str, pattern: str) -> bool:
    if "/" in pattern:
        return fnmatch(rel_posix, pattern)
    return fnmatch(name, pattern)


def is_excluded(rel_posix: str, extra: list[str] | None = None) -> bool:
    """Whether a file at rel_posix is excluded from copying.

    Directory exclusions are expressed as patterns too (dir name or dir/
    prefix); callers pass each ancestor directory as a trailing-slash
    pattern via iter_source_files.
    """
    name = rel_posix.rsplit("/", 1)[-1]
    for pattern in EXCLUDED_FILE_PATTERNS:
        if _match_pattern(rel_posix, name, pattern):
            return True
    for pattern in extra or []:
        if _match_pattern(rel_posix, name, pattern):
            return True
    return False


def iter_source_files(src: Path, extra_excludes: list[str] | None = None):
    """Yield (relative posix path, absolute path) for copyable files."""
    extra = list(extra_excludes or [])
    for path in sorted(src.rglob("*")):
        if path.is_symlink():
            yield path.relative_to(src).as_posix(), path
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(src).as_posix()
        parts = rel.split("/")[:-1]
        if any(part in EXCLUDED_DIR_NAMES for part in parts):
            continue
        if any(part in extra for part in parts):
            continue
        if is_excluded(rel, extra):
            continue
        yield rel, path
