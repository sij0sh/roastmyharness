# BENCHMARKS.md

Log of A/B experiments run with RoastMyHarness. One section per
experiment, newest first. Each section records identity, design,
results, and interpretation so numbers stay auditable after the fact.

How to read:

- Resolve rate alone is noise at small n. Paired flips are the signal:
  which tasks flipped from fail to pass (rescued) or pass to fail
  (broken) between arms.
- Smoke runs (1 task) exist to validate plumbing, not to rank agents.
- Cost is list-price basis for claude arms (Claude Code's own
  accounting) and blank for pi arms on the gateways, which do
  not surface per-call costs.

---

## Claude Code bare vs Pi bare

First harness-only comparison: both arms ran the SAME model at the
SAME thinking level, so any difference isolates the harness, not the
model.

### Identity

| field | value |
|---|---|
| Run id | `pi-vs-claude-bare-ed2d5b93` |
| Date | 2026-09-09 |
| Spec | `pi-vs-claude-bare.toml` |
| Branch | `ad-hoc/claude-bare` (Claude arm support restored from `8585ce8`) |
| Harness | roastmyharness 0.1.0, pier 0.3.1 |
| Model (both arms) | `anthropic-gateway/claude-opus-5` |
| Thinking (both arms) | `low` |
| Pi arm | pi 0.85.1 (`pi_version = "latest"` at prepare) |
| Claude arm | Claude Code 2.1.266 (npm-pinned) |
| Task corpus | bundled DeepSWE, preset `luna-signal`, catalog rev 2026-09 |
| Task hash | `90592907baf82f7bcd84c4eccce0b1229c63711f0e6e8ecf19c362ce01b6f7a0` |
| Run dir | `~/.local/share/roastmyharness/runs/pi-vs-claude-bare-ed2d5b93` |

### Design

Smoke scale: one task, one repetition per arm, concurrency 1.

Task: `boa-hierarchical-evaluation-cancellation` (DeepSWE, medium
difficulty band). Rust work in the boa_engine JS engine: implement
evaluation cancellation semantics (cancel handles, job skipping,
parent/child reason propagation) behind a verifier of 24 cargo-nextest
tests - 17 fail-to-pass and 7 pass-to-pass. The task grades to 1.0
only when all 24 pass.

Fairness contracts (bare means bare):

| arm | contract |
|---|---|
| pi control | `-nc --no-skills --no-prompt-templates --no-themes`, no extensions, no skills |
| claude | `--strict-mcp-config`, pinned settings.json (`bypassPermissions`, telemetry/auto-update off), no MCP servers |

Both arms ran the identical instruction, container image, egress
allowlist (model gateway + npm), and git identity.

### Results

| metric | pi bare | Claude Code bare |
|---|---|---|
| Outcome | FAIL (reward 0.0) | PASS (reward 1.0) |
| Verifier | 23/24 tests | 24/24 tests |
| Wall time | 21 min | 27 min |
| Input tokens (cached) | 2,651k | 5,145k |
| Input tokens (uncached) | ~0 | 5,283k total / 138.6k cache-write |
| Output tokens | 32k | 49,866 (incl. 15,775 thinking) |
| Cost | not surfaced by gateway | $4.78 (list-price basis) |
| Tool calls | 50 | 61 |
| Turns / recorded steps | - | 62 turns, 56 ATIF steps |
| Peak context tokens | 81k | not captured (see provenance) |

Paired flips (claude vs control): 1 broken flip - claude passed the
task pi failed. No rescued flips, no both-pass.

### Pi's near miss

Pi scored 23/24. Every pass-to-pass test held (no regressions), and 16
of 17 fail-to-pass tests flipped green. One fail-to-pass test stayed
red:

```
[f2p] boa_engine: tests::evaluation::
  cancelled_session_jobs_are_skipped_but_unrelated_jobs_still_run
```

The semantic gap is precise: pi's implementation got cancellation to
stop execution, propagate reasons to children, and reject enqueueing
onto cancelled handles - but its job-skipping path did not preserve
the "unrelated jobs still run" invariant when a session is cancelled.
Under DeepSWE's all-or-nothing verifier that is reward 0.0 despite a
~96% correct implementation. This is exactly the near-miss band the
medium difficulty label targets, and it is the most useful single
data point of the smoke: the arms differ by one semantic edge case,
not by capability.

Claude Code passed all 17 fail-to-pass and all 7 pass-to-pass tests.

### Behavioral notes (n=1, anecdotal)

- Claude burned ~2x pi's cached input (5.1M vs 2.7M) and took 6
  minutes longer; thinking contributed 15.8k of its 50k output.
- Pi's failure mode was semantic (one edge case), not mechanical: no
  errors, no empty patch, no timeout.
- Claude's transcript shows heartbeat keep-alives during long rust
  builds; wall-time comparisons on build-heavy tasks carry that noise.

### Telemetry provenance (incident disclosure)

The claude arm's token/cost cells initially reported zero. Cause:
Claude Code writes its transcript and `.claude.json` mode 0600/0700
as the container user; pier's host-side relocate preserved that, so
the ATIF converter failed with PermissionError and silently dropped
all metrics. Fixed in `fe030e4` (scan tolerance) and `9c69b96`
(container-side chmod after agent exit). The numbers above were then
recovered from this same trial's artifacts: transcript converted with
pier's own ATIF converter, metrics folded into `result.json`, report
regenerated. Nothing was re-run; the pass/fail verdicts were never in
question. Cost is Claude Code's list-price accounting; actual gateway
cost differs.

### Interpretation

- n=1: no ranking signal. The single paired flip is consistent with
  harness differences in prompt wiring, tool ergonomics, or luck.
- Next step when resuming this experiment: set `include = ["*"]` in
  `pi-vs-claude-bare.toml` for the 30-task `luna-signal` screen,
  `per_variant = 2`. Expect roughly $140-200 list-price for the
  claude arm at this per-task cost.
- Watch item: whether pi's near-miss pattern (many tests green, one
  semantic edge case red) repeats on other tasks; all-or-nothing
  verifiers punish it disproportionately.

### Reproduce

```bash
roastmyharness validate pi-vs-claude-bare.toml
roastmyharness run pi-vs-claude-bare.toml
roastmyharness watch <run-id>
```

Requires `GATEWAY_API_KEY` exported, docker running, and the
`ad-hoc/claude-bare` branch (Claude arm support is not on main).

---

## Adding a new experiment section

Append above the oldest section. Minimum fields: run id, date, spec
file, branch/harness versions, model and thinking per arm, task
corpus and scale, results table, paired flips, interpretation, and
any incident affecting the numbers. Disclose telemetry gaps and
post-hoc recoveries inline - never silently.
