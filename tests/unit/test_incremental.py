"""Incremental tracker: partial lines, append, truncation, replacement."""

from __future__ import annotations

from pathlib import Path

from roast_my_harness.telemetry.parser import IncrementalTracker


def test_appends_and_partials(tmp_path: Path):
    f = tmp_path / "e.jsonl"
    f.write_text('{"a":1}\n{"a')
    tracker = IncrementalTracker()
    assert list(tracker.read(f)) == ['{"a":1}']
    f.write_text('{"a":1}\n{"a":2}\n{"b')
    lines = list(tracker.read(f))
    assert lines == ['{"a":2}']
    f.write_text('{"a":1}\n{"a":2}\n{"b":3}\n')
    assert list(tracker.read(f)) == ['{"b":3}']
    assert list(tracker.read(f)) == []


def test_truncation_resets(tmp_path: Path):
    f = tmp_path / "e.jsonl"
    f.write_text('{"x":1}\n{"x":2}\n')
    tracker = IncrementalTracker()
    assert len(list(tracker.read(f))) == 2
    f.write_text('{"y":1}\n')
    assert list(tracker.read(f)) == ['{"y":1}']


def test_missing_file_yields_nothing(tmp_path: Path):
    tracker = IncrementalTracker()
    assert list(tracker.read(tmp_path / "nope.jsonl")) == []
