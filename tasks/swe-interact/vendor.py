"""Vendor SWE-Interact multiturn tasks into Roast with the 4 documented diffs.

Usage: .venv/bin/python tasks/swe-interact/vendor.py <upstream-task-dir>...
Copies each upstream task dir, applies PROVENANCE.md diffs 1-4 with
assertions, and fails loudly when an anchor is missing.

Diffs:
  1. task.toml top-level artifacts -> ["/logs/artifacts/model.patch"]
  2. task.toml [environment].docker_image line removed (force pier build)
  3. user-server/server.py max_tokens=32768 -> max_completion_tokens=32768
  4. ownership hardening: EXIT-trap chmod in tests/test.sh and
     steps/05_test_handoff/tests/test.sh; 05 finale exec -> run/chmod/exit
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRAP = "trap 'chmod -R a+rwX /logs/agent /logs/artifacts /logs/verifier 2>/dev/null || true' EXIT"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{path}: anchor found {count}x, expected 1x: {old[:70]!r}")
    path.write_text(text.replace(old, new))


def remove_line_containing(path: Path, needle: str) -> None:
    lines = path.read_text().split("\n")
    hits = [line for line in lines if needle in line]
    if len(hits) != 1:
        raise ValueError(f"{path}: {needle!r} found {len(hits)}x, expected 1x")
    lines.remove(hits[0])
    path.write_text("\n".join(lines))


def vendor_task(src: Path) -> Path:
    if not (src / "task.toml").is_file():
        raise ValueError(f"not a task dir: {src}")
    if not (src / "steps" / "05_test_handoff").is_dir():
        raise ValueError(f"not a 5-step SWE-Interact task: {src}")
    dst = ROOT / src.name
    if dst.exists():
        raise ValueError(f"already vendored: {dst}")
    shutil.copytree(src, dst)

    task_toml = dst / "task.toml"
    text = task_toml.read_text()
    head, sep, tail = text.partition("[task]")
    if not sep or head.count("artifacts = []") != 1:
        raise ValueError(f"{task_toml}: top-level artifacts anchor != 1x")
    task_toml.write_text(
        head.replace(
            "artifacts = []", 'artifacts = ["/logs/artifacts/model.patch"]'
        )
        + sep
        + tail
    )
    remove_line_containing(task_toml, "docker_image = ")

    server = dst / "environment" / "user-server" / "server.py"
    text = server.read_text()
    if text.count("max_tokens=32768") != 2:
        raise ValueError(f"{server}: max_tokens anchor count != 2")
    server.write_text(text.replace("max_tokens=32768", "max_completion_tokens=32768"))

    finale = dst / "steps" / "05_test_handoff" / "tests" / "test.sh"
    replace_once(
        finale,
        "exec bash /tests/canonical_test.sh\n",
        "rc=0\nbash /tests/canonical_test.sh || rc=$?\n"
        "chmod -R a+rwX /logs/agent /logs/artifacts /logs/verifier 2>/dev/null || true\n"
        "exit $rc\n",
    )
    anchor = "mkdir -p /logs/agent /logs/artifacts /logs/verifier\n"
    replace_once(finale, anchor, anchor + TRAP + "\n")

    top = dst / "tests" / "test.sh"
    top_text = top.read_text()
    if "trap " in top_text:
        raise ValueError(f"{top}: unexpected existing trap")
    top_lines = top_text.split("\n")
    idx = next(
        i for i, line in enumerate(top_lines) if line.startswith("log()")
    )
    top_lines.insert(idx, TRAP)
    top_lines.insert(idx + 1, "")
    top.write_text("\n".join(top_lines))
    return dst


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    for raw in argv[1:]:
        dst = vendor_task(Path(raw).expanduser().resolve())
        print(f"vendored {dst.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
