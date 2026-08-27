# RoastMyHarness

Standalone terminal tool that compares Pi extensions and skills on identical
Pier tasks. Successor to the DSE-tests script harness.

## What it does

- One declarative TOML or YAML file defines control + variants (no Python edits).
- Runs one Pier job per variant with the same model, thinking level, and
  fairness flags (`-nc --no-skills --no-prompt-templates --no-themes`).
- Headless progress: `status <id>` prints the live matrix and totals;
  `watch <id>` streams a live ASCII matrix until the run finishes.
- Ctrl-C or SIGTERM leaves completed trials resumable; `resume` runs only
  missing cells.
- Run, resume, and report take an exclusive per-experiment lock. `status` is
  read-only and can observe a running experiment.
- Writes structured redacted diagnostics to `<run>/logs/run.jsonl`.
- Writes `summary.csv` (DSE-tests-compatible schema), `summary.json`, and
  `report.md` automatically on completion.
- Reuses Pi's Codex OAuth from `~/.pi/agent/auth.json` (staged per job,
  mode 0600; cached homes stay secret-free).

## Install

    uv tool install .

Python 3.12+, Pier 0.3.x (`uv tool install datacurve-pier`), docker or a
docker-compatible shim, node/npm inside task containers (installed by the
adapter).

## Uninstall

    uv tool uninstall roastmyharness
    rm -rf ~/.local/share/roastmyharness   # database + run outputs
    rm -rf ~/.cache/roastmyharness         # cached Pi homes
    
Run outputs live under the data dir unless `ROAST_MY_HARNESS_RUNS_DIR`
is set. Set `ROAST_MY_HARNESS_DEBUG=1` to re-raise an unexpected CLI
exception with its traceback. The tool stores no credentials of its own; it reuses
`~/.pi/agent/auth.json`, which belongs to Pi and is not removed here.

## Quick start

    roastmyharness init my-experiment.toml   # commented starter spec
    $EDITOR my-experiment.toml
    roastmyharness validate my-experiment.toml
    roastmyharness run my-experiment.toml

Other commands: `resume <id>`, `status <id>`, `report <id>`, `list`,
`auth status`, `setup [--agent pi|claude
--scope user|project]`, `doctor`, plus `--json` on validate/status/list.

## Pi slash command and integrations

The Pi extension provides `/roastmyharness [task-root]`.
The command follows
a fixed wizard:

1. Describe which variants to run and how many.
2. Include or exclude a control. An included control can be fresh or historic.
3. Select an available Pi model.
4. Select one task, the full task set, or a custom task count or list.
5. Review the generated YAML and validated trial plan.
6. Choose `Confirm and run`, `Change`, or `Cancel`. `Change` accepts a free-form
   revision and repeats generation, validation, and review.

A read-only Pi SDK agent converts the open variant description and change
requests into YAML. The wizard gives the agent verified metadata for local Pi
packages configured in user or project settings. The agent can also inspect
files with read-only tools. Schema, source, auth, and pinned npm availability
checks still gate plan approval. Accepted plans launch immediately. Generated
files are kept under `.pi-files/roastmyharness/`. The extension also retains
the `roast_harness` tool for status, watch, cancel, and report operations. `watch` streams
per-trial results, running cells, final aggregates, and report paths.

There is no integration skill to invoke implicitly. Install the Pi command or
the Claude MCP server idempotently:

    roastmyharness setup --agent pi --scope user
    roastmyharness setup --agent claude --scope project
    roastmyharness doctor

## Experiment spec

The loader accepts TOML and YAML. `roastmyharness init` still writes a
commented TOML starter. See `examples/` for more TOML examples. Key sections
are `[model]`, `[tasks]`,
`[concurrency]`, `[control]`, and one `[[variants]]` block per arm with
local extensions (`kind = "local"`), pinned npm packages (`kind = "npm"`),
skills, env pins, and typed setup handlers.

`[model] provider` accepts any provider defined in the host pi
`~/.pi/agent/models.json` (model ids are validated against it), the
default `openai-codex` (auth via `pi /login codex`), or `custom` with
`provider_id` + `models_json`. Host providers stage automatically: the
provider block is sliced per job, referenced env vars must be set, and
`!command` apiKeys are rejected.

## Status (build phases)

- Phase 0-3: done (spec, homes, adapter, headless runner, telemetry,
  reports, resume, cancellation).
- Phase 4 (TUI): removed - CLI-first pivot. The Textual TUI is archived,
  untracked, under `.pi-files/tui-archive/`; the `textual` dependency is
  gone and bare `roastmyharness` prints help.
- Phase 5 (auth): Phase A done (reuse pi Codex OAuth, status, staging).
  Integrated OAuth bridge is a follow-up; use `pi /login codex`.
- Phase 6 (historic controls): done - eligible-pool reuse planning
  (minimum runs, age limits, never/ask/require), sentinel-gated release
  with exact Poisson-binomial drift test, H-cell matrix state, and report
  disclosure.

Golden parity: `tests/golden/test_dse_parity.py` reproduces legacy
DSE-tests collect.py values on a scrubbed real trial
(bare/datacurve/abs-stepped-slices).

Deferred by design (plan section 2): remote fan-out, non-Pier benchmarks,
other agents, web dashboards, cloud storage, automatic stopping.
