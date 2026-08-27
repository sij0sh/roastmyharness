# RoastMyHarness

Standalone terminal tool that compares Pi extensions and skills on identical
Pier tasks. Successor to the DSE-tests script harness.

## What it does

- One declarative TOML file defines control + variants (no Python edits).
- Runs one Pier job per variant with the same model, thinking level, and
  fairness flags (`-nc --no-skills --no-prompt-templates --no-themes`).
- Headless progress: `status <id>` prints the live matrix and totals.
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

    uv tool uninstall roast-my-harness
    rm -rf ~/.local/share/roast-my-harness   # database + run outputs
    rm -rf ~/.cache/roast-my-harness         # cached Pi homes
    
Run outputs live under the data dir unless `ROAST_MY_HARNESS_RUNS_DIR`
is set. Set `ROAST_MY_HARNESS_DEBUG=1` to re-raise an unexpected CLI
exception with its traceback. The tool stores no credentials of its own; it reuses
`~/.pi/agent/auth.json`, which belongs to Pi and is not removed here.

## Quick start

    roast-my-harness init my-experiment.toml   # commented starter spec
    $EDITOR my-experiment.toml
    roast-my-harness validate my-experiment.toml
    roast-my-harness run my-experiment.toml

Other commands: `resume <id>`, `status <id>`, `report <id>`, `list`,
`import-dse <results-dir>`, `auth status`, `setup [--agent pi|claude
--scope user|project]`, `doctor`, plus `--json` on validate/status/list.

## Agent skill and extension

`.agents/skills/roast-my-harness/SKILL.md` lets Pi or Claude drive
experiments from plain-language requests; the `roast_harness` tool wraps
the same service with JSON responses. Install integrations idempotently
instead of symlinking by hand:

    roast-my-harness setup --agent pi --scope user
    roast-my-harness setup --agent claude --scope project
    roast-my-harness doctor

## Experiment spec

See `examples/` and `roast-my-harness init`. Key sections: `[model]`, `[tasks]`,
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
  gone and bare `roast-my-harness` prints help.
- Phase 5 (auth): Phase A done (reuse pi Codex OAuth, status, staging).
  Integrated OAuth bridge is a follow-up; use `pi /login codex`.
- Phase 6 (historic controls): done - eligible-pool reuse planning
  (minimum runs, age limits, never/ask/require), sentinel-gated release
  with exact Poisson-binomial drift test, H-cell matrix state, report
  disclosure, `import-dse` for legacy results (imported observations are
  ineligible for automatic reuse because legacy artifacts lack
  pi_version/adapter provenance).

Golden parity: `tests/golden/test_dse_parity.py` reproduces legacy
DSE-tests collect.py values on a scrubbed real trial
(bare/datacurve/abs-stepped-slices).

Deferred by design (plan section 2): remote fan-out, non-Pier benchmarks,
other agents, web dashboards, cloud storage, automatic stopping.
