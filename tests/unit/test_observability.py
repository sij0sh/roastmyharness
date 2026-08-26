"""Structured diagnostics stay useful without storing credential values."""

from __future__ import annotations

import json
from pathlib import Path

from roast_my_harness.observability import RunLogger, redact_text


def test_redact_text_masks_common_credentials():
    text = "Authorization: Bearer abc123 sk-secret-value"
    redacted = redact_text(text)
    assert "abc123" not in redacted
    assert "sk-secret-value" not in redacted
    assert "[REDACTED]" in redacted


def test_run_logger_writes_jsonl_and_redacts_values(tmp_path: Path):
    path = tmp_path / "logs" / "run.jsonl"
    RunLogger(path, "experiment-1").emit(
        "failure",
        variant="a",
        task="t1",
        token="secret-token",
        message="Bearer abc123",
    )
    event = json.loads(path.read_text())
    assert event["event"] == "failure"
    assert event["experiment_id"] == "experiment-1"
    assert event["token"] == "[REDACTED]"
    assert "abc123" not in path.read_text()
