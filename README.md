# RoastMyHarness

Standalone terminal tool that compares Pi extensions and skills on identical
Pier tasks. Successor to the DSE-tests script harness.

## What it does

- One declarative TOML file defines control + variants (no Python edits).
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
2. Include or exclude a control. An included control always runs fresh.
3. Select an available Pi model.
4. Select a supported thinking level.
5. Select a curated test suite: GPT-5.6 Luna High or GLM-5.3-Flash Max.
6. Select one random task, the curated 30 (signal screen), the curated 60
   (signal + confirmation), the full task set, or a custom random count.

After the selections, a `roast_harness author` card appears in Pi's session.
The card runs an ephemeral Pi child context with read-only filesystem tools,
streams its source checks and spec draft, writes the TOML under
`.pi-files/roastmyharness/`, and validates it. Invalid generated specs get up to
two focused repair attempts. The parent session receives only the bounded tool
result instead of the authoring conversation. No authoring status is shown in
Pi's footer.

Schema, source, auth, and pinned npm availability checks gate approval. The
agent presents the validated plan and must receive explicit approval before it
calls `roast_harness start`.

The `start` and `watch` actions render a second live session card. It updates
in place with completion percentage, per-variant counts, active cells, recent
trial results, final aggregates, and report paths. Expand the card with Pi's
configured tool-expand key to see the task matrix. Aborting the card detaches
the watcher but does not stop the experiment; use `cancel` to stop it.

There is no integration skill to invoke implicitly. Install the Pi command or
the Claude MCP server idempotently:

    roastmyharness setup --agent pi --scope user
    roastmyharness setup --agent claude --scope project
    roastmyharness doctor

## Bundled DeepSWE benchmark

The DeepSWE task corpus lives in this repo under `tasks/deepswe/` (117 Harbor
format tasks with `task.toml`, `instruction.md`, `environment/`, `tests/`, and
`solution/`; see `tasks/deepswe/README.md` and `PROVENANCE.md`). The Pi
extension resolves the benchmark relative to its own installed symlink, so the
wizard defaults to these local tasks with no external checkout or network
access. Curated suite membership lives in `tasks/deepswe/suites.json`: per
model (Luna High, GLM-5.3-Flash Max), a 30-task signal screen of frontier tasks
(1/4-3/4 historical rollout outcomes) plus a 30-task confirmation extension
with regression/floor anchors. Passing `/roastmyharness <path>` still overrides
the bundled root, and other task roots are discovered from the working directory
and recent runs.

## Experiment spec

The loader accepts TOML only. `roastmyharness init` still writes a
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
- Phase 6 (historic controls): removed - control reuse never fired in
  practice, so controls always run fresh. The `control_observations`
  store, reuse planning, and the sentinel drift gate are gone; `[control]`
  accepts only `enabled`.

summary.csv carries a tool-owned schema; columns may change between
releases. The legacy DSE-parity golden tests were removed.

Deferred by design (plan section 2): remote fan-out, non-Pier benchmarks,
other agents, web dashboards, cloud storage, automatic stopping.
