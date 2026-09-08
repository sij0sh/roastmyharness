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
2. Choose a fresh control, require matching historic controls, or exclude the control.
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

The `start` and `watch` actions render a second live session card. The
command instead launches through the same shared start path and returns
immediately, so the prompt box stays live: progress streams into a widget
above the editor. When authoring finishes, the command posts a persistent
Spec author transcript card (plan summary, model, attempts, token usage,
elapsed time); when the run ends, it posts a persistent Benchmark transcript
card (completion counts, token totals, rate, aggregates, report paths) plus
a final notification. Both cards render exactly like the `roast_harness`
tool cards and support Pi's configured tool-expand key for the full matrix
and spec preview. While the run is tracked,
the `roast_harness` tool stays visible so you can ask the session for updates
or to cancel the run, and re-running `/roastmyharness` offers Watch live,
Show status, Cancel run, or Start a new run instead of the wizard. Aborting
the card detaches the watcher but does not stop the experiment; use `cancel`
to stop it.

There is no integration skill to invoke implicitly. Install the Pi command or
the Claude MCP server idempotently:

    roastmyharness setup --agent pi --scope user
    roastmyharness setup --agent claude --scope project
    roastmyharness doctor

## Bundled DeepSWE benchmark

The DeepSWE task corpus lives in this repo under `tasks/deepswe/` (115 runnable
tasks with `task.toml`, `instruction.md`, `environment/`, `tests/`, and
`solution/`; see `tasks/deepswe/README.md` and `PROVENANCE.md`). The Pi
extension resolves the benchmark relative to its own installed symlink, so the
wizard defaults to these local tasks with no external checkout or network
access. Preset membership lives in `tasks/deepswe/tasks/catalog.toml`: per
model (Luna High, GLM-5.3-Flash Max), a 30-task signal screen of frontier tasks
plus a 30-task confirmation extension. Passing `/roastmyharness <path>` still
overrides the bundled root, and other task roots are discovered from the
working directory and recent runs.

Release bundling decision: the wheel ships the corpus and the Pi extension
inside the package (`roast_my_harness/bundled/`, ~4.6 MB compressed for
33 MB on disk), so `setup` and discovery work from a bare `pip install`
with no checkout. `setup` prefers a repo checkout when one is present
(`ROAST_MY_HARNESS_REPO` overrides); `profiles` and `tool catalog` fall
back to the bundled corpus only when the default `./tasks/deepswe/tasks`
is absent, so an explicit path typo still errors instead of retargeting.

## Custom evaluations

DeepSWE is one evaluation, not the unit of the harness. An experiment
selects an eval via `[evaluation]`: `type = "bundled"` (today only
`id = "deepswe"`, the default when the block is absent),
`type = "generated"` (a frozen custom eval described by `eval.toml`
beside the task root), or `type = "external"` (a plain local Pier task
set with an optional `eval.toml`). Eval identity (type, id, revision,
contract hash) enters run identity, the run manifest, and
historic-control cohort keys, so different evals never share cells or
history.

A custom eval is a frozen contract, not a folder of tasks:

- `eval.toml` states the scoring bar (`[scoring] pass_threshold`) and
  pins the judge (`[judge]` model, rubric, samples) when the eval uses
  one. Verifiers fold dimensions into the scalar `reward` per this
  contract; reports render deterministic and judge scores separately.
- `validation/self-test.json` ships synthetic verifier outputs with
  expected outcomes. `validate`/`run` refuse to launch unless every
  fixture resolves as expected and the set discriminates (at least one
  expected pass and one failure). An undeclared judge score, or a judge
  score without `judge_model`, fails the gate.
- Authoring order matters: map capabilities first, design tests from
  the map alone (never from the skill source), freeze validators, add
  fixtures, run a critic pass over the checklist in
  `examples/evals/structured-output/tasks/validation/critic.json`, then
  calibrate on bare control only. Once any variant-under-test has run,
  the eval is immutable.

See the hand-built example (`examples/evals/structured-output/` with
`examples/structured-output-eval.toml`): three deterministic tasks, a
worked capability map and rationale, and verifiers whose checks stay
host-testable via `APP_DIR`/`LOGS_DIR` overrides.

Start a new eval with `roastmyharness eval init <dir> --id <eval-id>`
and check it with `roastmyharness eval validate <dir>`: the validator
covers the descriptor, capability map, rationale, tasks, critic
verdict, and fixture self-tests, and fails until every step is
complete. The Pi wizard offers Recommended (bundled DeepSWE) /
Custom (frozen generated eval) / Existing (external task set) modes
and freezes the choice into `[evaluation]`, so benchmark content can
never change after a run begins.

## Experiment spec

The loader accepts TOML only. `roastmyharness init` still writes a
commented TOML starter. See `examples/` for more TOML examples. Key sections
are `[model]`, `[tasks]`, `[evaluation]`,
`[concurrency]`, `[control]`, and one `[[variants]]` block per arm. Controls
run fresh by default (`mode = "fresh"`). `mode = "historic"` reuses eligible
history: `history_scope = "hybrid"` runs fresh controls for tasks without
history, `"intersection"` runs only the history-backed test intersection.
`minimum_runs_per_task`, `maximum_age_days`, and `sentinel_tasks` bound the
pool; sentinels sample from history-backed tasks only and gate a
drift verdict (`accepted` / `rejected_drift` / `inconclusive`), with
`on_drift` / `on_inconclusive` selecting a fresh fallback or abort. There is
no stored interactive mode. Accepted history appears as `H`; reports disclose
its age, counts, drift verdict, and a per-task historical baseline next to
fresh extension rates.
Variant blocks support local extensions (`kind = "local"`), pinned npm packages (`kind = "npm"`),
skills, env pins, and typed setup handlers. `[execution]` sets
`repetitions` (independent scored rollouts per task, default 1) and
`max_retries` (relaunches per errored trial, default 1).
`[tasks] preset` selects a named list from the benchmark catalog
(`tasks/deepswe/tasks/catalog.toml`, e.g. `preset = "luna-signal"`);
include/exclude globs filter the preset further. Task metadata
(duration/difficulty/smoke) lives in the catalog, never inside task
directories, and runs record its `catalog_hash` next to the task hashes.

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
| `pi` | `pi` | `@earendil-works/pi-coding-agent` | `pi` | `pi_version` (global) | `latest` |
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
- `pi_version = "latest"` (the default) resolves to the newest npm release
  once at prepare time; the frozen version defines the run id and is what
  lands in the staged home, the run manifest, and reports. A moved `latest`
  starts a new run instead of reusing old cells. Pin `pi_version = "x.y.z"`
  for a reproducible version instead.
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
- Phase 6 (historic controls): done, then redesigned for v2. Deterministic
  `fresh` / `historic` modes use age-bounded observation pools and a fresh
  sentinel drift gate over history-backed tasks only; `hybrid` and
  `intersection` scopes decide what runs without history. The default
  `fresh` mode preserves fresh control behavior.

summary.csv carries a tool-owned schema; columns may change between
releases. Tool failure metrics (`tool_results`, `tool_failures`,
`tool_failure_rate`, `tool_missing_results`) derive from the normalized
ATIF trajectory every adapter writes, so they work for any agent; Pi-only
enrichment (context-manager counters and friends) lives under each trial's
`custom_metrics` object in summary.json, never in the CSV. The legacy
DSE-parity golden tests were removed.

Deferred by design (plan section 2): remote fan-out, non-Pier benchmarks,
agents beyond the registry (codex, gemini, opencode - the registry/adapter
pattern is in place for them), web dashboards, cloud storage, automatic
stopping.
