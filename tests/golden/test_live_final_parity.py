"""Golden: live (incremental) metrics equal final metrics on completion.

Plan section 14: one parsing module, two callers; live stats must equal
final stats once a trial finishes.
"""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.telemetry.parser import (
    IncrementalTracker,
    final_event_metrics,
    fold_event,
    fold_sidecar_line,
    fold_tool_event,
    new_event_metrics,
    new_tool_metrics,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"


def live_metrics(trial_dir: Path, chunk_size: int = 2) -> dict:
    """Replay the fixture file in small chunks like a poller would."""
    tracker = IncrementalTracker()
    m: dict = {**new_event_metrics(), **new_tool_metrics()}
    events = trial_dir / "agent" / "pi-events.jsonl"
    raw = events.read_text()
    # Feed the tracker arbitrary splits, including mid-JSON breaks.
    pos = 0
    piece = ""
    while pos < len(raw) or piece:
        piece += raw[pos : pos + 7]
        pos += 7
        complete, piece = _split_complete(piece)
        fake = events.with_name(".stream.tmp")
        fake.write_text(complete)
        for line in tracker.read(fake):
            event = json.loads(line)
            fold_event(m, event)
            if event.get("type") == "tool_execution_start":
                fold_tool_event(m, event)
    for line in (trial_dir / "agent" / "pi-event-times.log").read_text().splitlines():
        fold_sidecar_line(m, line)
    from roast_my_harness.telemetry.parser import finalize_metrics

    return finalize_metrics(m)


def _split_complete(data: str) -> tuple[str, str]:
    lines = data.split("\n")
    partial = lines.pop()
    return "\n".join(lines) + ("\n" if lines else ""), partial


def test_live_equals_final():
    trial = FIXTURES / "trial"
    live = live_metrics(trial)
    final = final_event_metrics(trial)
    assert live == final
