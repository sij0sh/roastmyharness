#!/usr/bin/env python3
"""Phase-aware scoring for the compound task (task-local, NOT the shared grader).

Reads tests/config.json `phase_buckets` (phase -> [node ids]) and the per-phase
CTRF reports the verifier test.sh produced, and writes
/logs/verifier/phase_scores.json:

  {phase: {"passed": n, "total": m, "failed_ids": [...]}}

Same membership rule as the shared grader: an id missing from every report
counts as failed; duplicates merge worst-status-wins. Runs AFTER grader.py
grade; never affects reward.json (the aggregate F2P/P2P verdict stays
canonical and comparable with single-phase tasks).
"""
import json
import os
import sys
from pathlib import Path

TESTS_DIR = Path(os.environ.get("TESTS_DIR", "/tests"))
VERIFIER_DIR = Path(os.environ.get("VERIFIER_DIR", "/logs/verifier"))

RANK = {"passed": 0, "skipped": 1, "failed": 2}


def norm_status(raw):
    raw = str(raw or "").strip().lower()
    if raw == "passed":
        return "passed"
    if raw in ("skipped", "pending", "other"):
        return "skipped"
    return "failed"


def parse_ctrf(path):
    res = {}
    try:
        doc = json.loads(Path(path).read_text())
        tests = (doc.get("results") or {}).get("tests") or []
        if not isinstance(tests, list):
            return res
    except Exception:
        return res
    for tc in tests:
        if not isinstance(tc, dict):
            continue
        nm = str(tc.get("name") or "").strip()
        if not nm:
            continue
        # Match the shared grader's node_id="name" derivation: the CTRF
        # reporter already joins suite and test name, so the bare name is
        # the id. (No suite.name join here by design.)
        st = norm_status(tc.get("status"))
        cur = res.get(nm)
        if cur is None or RANK[st] > RANK[cur]:
            res[nm] = st
    return res


def main():
    cfg = json.loads((TESTS_DIR / "config.json").read_text())
    buckets = cfg.get("phase_buckets", {})
    reports = (cfg.get("grade", {}) or {}).get("reports", [])
    seen = {}
    for rep in reports:
        for nid, st in parse_ctrf(rep).items():
            cur = seen.get(nid)
            if cur is None or RANK[st] > RANK[cur]:
                seen[nid] = st
    out = {}
    for phase, ids in buckets.items():
        passed, failed_ids = 0, []
        for raw in ids:
            nid = str(raw).strip()
            if seen.get(nid) == "passed":
                passed += 1
            else:
                failed_ids.append(nid)
        out[phase] = {"passed": passed, "total": len(ids), "failed_ids": failed_ids}
    VERIFIER_DIR.mkdir(parents=True, exist_ok=True)
    (VERIFIER_DIR / "phase_scores.json").write_text(json.dumps(out, indent=2))
    for phase, s in out.items():
        print(f"[phase-score] {phase}: {s['passed']}/{s['total']} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
