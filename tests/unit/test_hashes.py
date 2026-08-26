"""Task and variant hash stability."""

from __future__ import annotations

from pathlib import Path

from roast_my_harness.homes.sources import source_tree_hash
from roast_my_harness.tasks.hashes import task_hash


def make_task(root: Path) -> Path:
    task = root / "t1"
    (task / "environment").mkdir(parents=True)
    (task / "task.toml").write_text('schema_version = "1.3"\n')
    (task / "instruction.md").write_text("do it\n")
    return task


def test_task_hash_stable(tmp_path: Path):
    a, b = make_task(tmp_path / "a"), make_task(tmp_path / "b")
    assert task_hash(a) == task_hash(b)


def test_task_hash_ignores_git_and_caches(tmp_path: Path):
    task = make_task(tmp_path)
    h1 = task_hash(task)
    (task / ".git").mkdir()
    (task / ".git" / "config").write_text("[core]")
    (task / "__pycache__").mkdir()
    (task / "__pycache__" / "x.pyc").write_text("junk")
    assert task_hash(task) == h1


def test_task_hash_changes_with_content(tmp_path: Path):
    task = make_task(tmp_path)
    h1 = task_hash(task)
    (task / "instruction.md").write_text("do it differently\n")
    assert task_hash(task) != h1


def test_source_hash_excludes_tests_and_locks(tmp_path: Path):
    src = tmp_path / "ext"
    (src / "src").mkdir(parents=True)
    (src / "src" / "index.ts").write_text("1")
    h1 = source_tree_hash(src)
    (src / "tests").mkdir()
    (src / "tests" / "x.test.ts").write_text("junk")
    (src / "package-lock.json").write_text("{}")
    (src / "node_modules").mkdir()
    (src / "node_modules" / "dep").mkdir()
    (src / "node_modules" / "dep" / "i.js").write_text("junk")
    (src / "AGENTS.md").write_text("leak")
    assert source_tree_hash(src) == h1
