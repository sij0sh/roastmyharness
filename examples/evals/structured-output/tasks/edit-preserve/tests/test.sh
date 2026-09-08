#!/bin/bash
# Deterministic verifier for edit-preserve: four preservation checks over
# /app/notes.txt, folded per the eval contract (reward 1.0 iff the
# fraction reaches pass_threshold 0.7).
#
# The seed literal duplicates environment/Dockerfile by design (see the
# eval rationale): the verifier must know the exact expected bytes.
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

SEED = """# Project notes

owner: ada
status: draft
budget: 1200

## Reminders
- water the plants
- rotate the logs
"""

path = os.path.join(os.environ["APP_DIR"], "notes.txt")
checks = []
try:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    checks.append(("exists", True))
except OSError:
    text = None
    checks.append(("exists", False))

if text is None:
    checks.extend([("status", False), ("preserved", False), ("line-count", False)])
else:
    lines = text.split("\n")
    seed_lines = SEED.split("\n")
    status = [line for line in lines if line.startswith("status:")]
    checks.append(("status", status == ["status: final"]))
    rest = [line for line in lines if not line.startswith("status:")]
    seed_rest = [line for line in seed_lines if not line.startswith("status:")]
    checks.append(("preserved", rest == seed_rest))
    checks.append(("line-count", len(lines) == len(seed_lines)))

passed = sum(1 for _, ok in checks if ok)
frac = passed / len(checks)
reward = 1.0 if frac >= 0.7 else 0.0
with open(os.path.join(os.environ["LOGS_DIR"], "reward.json"), "w",
          encoding="utf-8") as handle:
    json.dump({"reward": reward, "reward_deterministic": frac}, handle)
print(json.dumps({"passed": passed, "total": len(checks), "reward": reward,
                  "failed": [name for name, ok in checks if not ok]}))
EOF
