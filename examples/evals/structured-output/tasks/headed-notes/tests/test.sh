#!/bin/bash
# Deterministic verifier for headed-notes: five structure checks over
# /app/summary.md, folded per the eval contract (reward 1.0 iff the
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

path = os.path.join(os.environ["APP_DIR"], "summary.md")
checks = []
try:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    checks.append(("exists", True))
except OSError:
    text = None
    checks.append(("exists", False))

if text is None:
    lines = []
else:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]  # single trailing newline is required, not content


def heading_index(title):
    for i, line in enumerate(lines):
        if line == title:
            return i
    return None


if text is None:
    checks.extend([("first-line", False), ("order", False),
                   ("body", False), ("whitespace", False)])
else:
    first = heading_index("# Summary")
    findings = heading_index("## Findings")
    nexts = heading_index("## Next steps")
    checks.append(("first-line", first == 0))
    checks.append(("order", first is not None and findings is not None
                   and nexts is not None and first < findings < nexts))

    def has_body(index):
        return (index is not None and index + 1 < len(lines)
                and lines[index + 1].strip() != "")

    checks.append(("body", all(has_body(i) for i in (first, findings, nexts))))
    checks.append(("whitespace", all(line == line.rstrip() for line in lines)
                   and text.endswith("\n") and not text.endswith("\n\n")))

passed = sum(1 for _, ok in checks if ok)
frac = passed / len(checks)
reward = 1.0 if frac >= 0.7 else 0.0
with open(os.path.join(os.environ["LOGS_DIR"], "reward.json"), "w",
          encoding="utf-8") as handle:
    json.dump({"reward": reward, "reward_deterministic": frac}, handle)
print(json.dumps({"passed": passed, "total": len(checks), "reward": reward,
                  "failed": [name for name, ok in checks if not ok]}))
EOF
