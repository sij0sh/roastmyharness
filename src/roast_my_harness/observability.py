"""Redacted, structured diagnostics for an experiment run."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]+"),
)
_SECRET_KEYS = {"access", "refresh", "token", "apikey", "api_key", "authorization"}


def redact_text(value: str) -> str:
    """Replace common credential values before they reach a diagnostic log."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        def _replace(match: re.Match[str]) -> str:
            prefix = match.group(1) if match.lastindex else ""
            return f"{prefix}[REDACTED]"

        redacted = pattern.sub(_replace, redacted)
    return redacted


def contains_secret(value: str) -> bool:
    """Return whether text contains a supported credential pattern."""
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact credential-shaped mapping values."""
    if key and key.lower().replace("-", "_") in _SECRET_KEYS:
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(k): redact_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


@dataclass
class RunLogger:
    """Append JSONL diagnostics without allowing logging failures to stop a run."""

    path: Path
    experiment_id: str

    def emit(self, event: str, **fields: Any) -> None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "experiment_id": self.experiment_id,
            **{key: redact_value(value, key=key) for key, value in fields.items()},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            self.path.chmod(0o600)
        except OSError:
            pass
