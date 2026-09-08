#!/bin/bash
# Deterministic verifier for json-record: six exact-equality checks over
# /app/record.json, folded per the eval contract (reward 1.0 iff the
# fraction reaches pass_threshold 0.7).
#
# APP_DIR / LOGS_DIR override the container paths so the checks stay
# host-testable; in-container they default to /app and /logs/verifier.
set -u
APP_DIR="${APP_DIR:-/app}"
LOGS_DIR="${LOGS_DIR:-/logs/verifier}"
mkdir -p "$LOGS_DIR"
export APP_DIR LOGS_DIR
python3 - <<'EOF'
import json
import os

path = os.path.join(os.environ["APP_DIR"], "record.json")
checks = []

try:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    checks.append(("parses", True))
except (OSError, ValueError):
    data = None
    checks.append(("parses", False))

expected = {"name": "atlas", "version": "2.4.1",
            "tags": ["red", "green", "blue"], "enabled": True}
if isinstance(data, dict):
    checks.append(("name", data.get("name") == "atlas"))
    checks.append(("version", data.get("version") == "2.4.1"))
    checks.append(("tags", data.get("tags") == ["red", "green", "blue"]))
    checks.append(("enabled", data.get("enabled") is True))
    checks.append(("no-extras", set(data) == set(expected)))
else:
    checks.extend([("name", False), ("version", False), ("tags", False),
                   ("enabled", False), ("no-extras", False)])

passed = sum(1 for _, ok in checks if ok)
frac = passed / len(checks)
reward = 1.0 if frac >= 0.7 else 0.0
with open(os.path.join(os.environ["LOGS_DIR"], "reward.json"), "w",
          encoding="utf-8") as handle:
    json.dump({"reward": reward, "reward_deterministic": frac}, handle)
print(json.dumps({"passed": passed, "total": len(checks), "reward": reward,
                  "failed": [name for name, ok in checks if not ok]}))
EOF
