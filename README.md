# RoastMyHarness

Standalone terminal tool that compares Pi extensions and skills on identical
Pier tasks. Successor to the DSE-tests script harness.

## What it does

- One declarative TOML file defines control + variants (no Python edits).
- Runs one Pier job per variant with the same model, thinking level, and
  a per-agent fairness contract (pi:
  `-nc --no-skills --no-prompt-templates --no-themes`).
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

The Pi extension provides `/roastmyharness [text]`.
The command opens the wizard directly with no model call; freeform text
prefills step 1 (and still hints the task root). The command follows
a fixed wizard:

1. Describe which variants to run and how many.
2. Include or exclude a control. An included control always runs fresh.
3. Select an available Pi model.
4. Select a supported thinking level.
5. Select a curated test suite: GPT-5.6 Luna High or GLM-5.3-Flash Max.
6. Select one random task, the curated 30 (signal screen), the curated 60
   (signal + confirmation), the full task set, or a custom random count.

After the selections, one ephemeral Pi child context with read-only filesystem
tools authors the spec. It streams its source checks, tool activity, and spec
draft into the author card (`roast_harness author`) or, when the command runs
the wizard, into a live card above the editor (footer status shows the phase
and attempt). It writes the TOML under `.pi-files/roastmyharness/` and
validates it. Invalid generated specs get up to two focused repair attempts.
Author-child failures surface their error message in the card and the
failure notification. This author child is the only model call in the
command flow.

Schema, source, auth, and pinned npm availability checks gate launch. The
validated plan appears in a wizard screen with three choices: "Confirm and
launch" (the default; starts the experiment immediately with no further
model call), "Regenerate with feedback" (sends freeform feedback to the
author child for a revised spec), and "Cancel" (keeps the plan on disk).
The model-facing `roast_harness` tool keeps its own flow: the agent presents
the plan and must receive explicit approval before it calls
`roast_harness start`.

The `start` and `watch` actions render a second live session card (the
command wizard shows the same live progress in a widget above the editor
plus a final notification). It updates
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

## Agents

Experiments can compare coding agents, not just extensions. Every arm
resolves to `(agent, agent_version, model)`; the registry
(`src/roast_my_harness/adapter/registry.py`) names the supported set.

| agent | family | package | binary | version pin (spec key) | default |
|---|---|---|---|---|---|
| `pi` | `pi` | `@earendil-works/pi-coding-agent` | `pi` | `pi_version` (global) | 0.84.3 |
| `omp` | `pi` | `@oh-my-pi/pi-coding-agent` | `omp` | `agent_version` when `agent = "omp"` is the spec default | 18.0.9 |

Rules that keep arms comparable:

- All arms run the same global `[model]` and thinking level; reports
  render comparisons at equal (task, model).
- Each agent's fairness contract is registry-owned, never spec-owned:
  pi runs `--no-skills --no-prompt-templates --no-themes -nc`; omp runs
  `--no-skills` plus a staged `config.yml` that disables implicit
  provider configs (AGENTS.md/claude/codex/... auto-loading); the
  container installs Bun (pinned) because omp needs it.
- Cached homes never mix agents: `(agent, agent_version)` is part of the
  variant hash.
- `pi`-only features (extensions, skills, `pi_flags`, `npm_pi_install`
  setup) are rejected on other families with a naming error.

Credential staging per agent:

- `pi`: host provider block sliced into the staged home as `models.json`
  with `$VAR` refs (values resolved at run time), plus `auth.json`
  entries (Codex OAuth via `pi /login codex`) staged 0600 per job.
- `omp`: same providers, staged as `models.yml` with bare env names plus
  a `model-env.json` name list; the adapter resolves names into the run
  environment. Values never land in cached homes.

Example specs: `examples/omp-variant.toml` (pi control vs omp arm) and
`examples/cross-agent.toml` (omp-default spec with a pi control). Adding
an agent means one registry entry plus an adapter subclassing pier's
agent class; codex/gemini/opencode would follow that pattern.

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
agents beyond the registry (codex, gemini, opencode - the registry/adapter
pattern is in place for them), web dashboards, cloud storage, automatic
stopping.
