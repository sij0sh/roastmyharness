"""Portable host-platform coverage: locks and process control."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

from roast_my_harness.host_lock import ExperimentLock, lock_is_free
from roast_my_harness.host_process import VariantProcess, cancel_all, kill_after_grace


def test_lock_is_free_missing_file(tmp_path: Path):
    assert lock_is_free(tmp_path / "run") is True


def _write_holder(tmp_path: Path, run_dir: Path) -> Path:
    script = tmp_path / "hold_lock.py"
    script.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "from roast_my_harness.host_lock import ExperimentLock\n"
        f"run_dir = Path({str(run_dir)!r})\n"
        "lock = ExperimentLock(run_dir)\n"
        "lock.__enter__()\n"
        "print('held', flush=True)\n"
        "time.sleep(30)\n"
    )
    return script


def test_lock_is_free_held_by_other_process(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    child = subprocess.Popen(
        [sys.executable, str(_write_holder(tmp_path, run_dir))],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held"
        deadline = time.monotonic() + 15
        while lock_is_free(run_dir) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert lock_is_free(run_dir) is False
    finally:
        child.terminate()
        child.wait(timeout=15)
    assert lock_is_free(run_dir) is True


def test_lock_conflict_raises_in_other_process(tmp_path: Path):
    run_dir = tmp_path / "run"
    script = tmp_path / "take_lock.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from roast_my_harness.host_lock import ExperimentLock\n"
        "from roast_my_harness.errors import RunBusyError\n"
        f"run_dir = Path({str(run_dir)!r})\n"
        "try:\n"
        "    ExperimentLock(run_dir).__enter__()\n"
        "except RunBusyError:\n"
        "    raise SystemExit(10)\n"
        "raise SystemExit(11)\n"
    )
    with ExperimentLock(run_dir):
        child = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            timeout=30,
        )
        assert child.returncode == 10


def test_process_start_and_cancel(tmp_path: Path):
    async def main() -> None:
        proc = VariantProcess(
            "v",
            [sys.executable, "-c", "import time; time.sleep(60)"],
            tmp_path / "v.log",
        )
        await proc.start(env=None)
        assert proc.running
        await cancel_all([proc], grace_sec=5.0)
        assert not proc.running

    asyncio.run(main())


def test_kill_after_grace_on_exited_process(tmp_path: Path):
    async def main() -> None:
        proc = VariantProcess(
            "v", [sys.executable, "-c", "pass"], tmp_path / "v.log"
        )
        await proc.start(env=None)
        assert proc.proc is not None
        await proc.proc.wait()
        await kill_after_grace(proc, 1.0)

    asyncio.run(main())
