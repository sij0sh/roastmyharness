# Changelog

## Unreleased

### Added

### Changed

- `/roastmyharness` no longer routes through the model. The command opens
  the wizard directly; freeform text after the command prefills the
  variant-request step. The isolated spec author stays the only model
  call and streams its draft into a live card above the editor. The
  validated plan is shown in a wizard screen where
  "Confirm and launch" (default) starts the experiment deterministically
  and "Regenerate with feedback" re-runs the author with freeform
  feedback. Live watch progress renders in the same widget slot.
  Author-child failures surface their error message per attempt.
  The `roast_harness` tool path is unchanged for model-initiated
  authoring.

- Multi-agent comparison arms. Specs may set `agent` globally or per
  `[[variants]]` arm. The registry (`adapter/registry.py`) ships `pi`
  and `omp` (oh-my-pi, a pi-family fork running on Bun). Per-arm
  identity enters the variant hash, so cached homes never mix agents.
- omp arms stage `models.yml` + `model-env.json` with bare env names.
  pi arms keep `models.json` with `$VAR` refs. omp installs a pinned
  Bun in-container. Fairness contracts are registry-owned per agent.
  See `examples/omp-variant.toml` and `examples/cross-agent.toml`.

### Removed (breaking for [control] specs)

Control reuse is retired: control arms always run fresh. `control_reuse`
never fired across every recorded experiment, so the observation pool, reuse
planning, and the sentinel drift gate are gone.

- `[control]` now accepts only `enabled`. The `reuse`,
  `minimum_runs_per_task`, `maximum_age_days`, and `sentinel_tasks` keys are
  rejected; remove them from existing specs.
- A migration drops the `control_observations` table and deletes old rows.
- Status matrices no longer render `H` (reused) control cells; resumed runs
  that reused controls show those cells as pending.
- summary.csv is no longer bound to the legacy DSE-tests schema; the column
  set is tool-owned and may change between releases.

### Added

- The DeepSWE benchmark now ships inside the repo under `tasks/deepswe/` (117
  Harbor tasks plus upstream README, PROVENANCE, and LICENSE). The Pi wizard
  resolves it through its installed symlink and defaults to it; an explicit
  `/roastmyharness <path>` argument still wins.
- The Pi wizard asks for a curated test suite: GPT-5.6 Luna High or
  GLM-5.3-Flash Max. It then offers one random task, the curated 30 (signal
  screen), the curated 60 (signal + confirmation), the full task set, or a
  custom random count.
- Suite membership lives in `tasks/deepswe/suites.json`. The wizard validates
  curated picks against the discovered task ids and aborts on missing ids.
- The Pi integration now provides the `/roastmyharness` command.
- The linear wizard collects variants, control mode, model, thinking mode, and
  task count. Its `roast_harness author` action now runs spec authoring in an
  ephemeral Pi child context and streams source checks, drafting, repair, and
  validation into a custom session card. Validation problems render as one
  compact line, clear before each repair attempt, and appear only after the
  final attempt. The footer authoring notification is no longer used.
- Live progress streaming for `roast_harness`. `AgentService.watch` emits
  read-only NDJSON events: snapshot, trial, state, heartbeat, and a final
  event with aggregates and report paths. It polls the same filesystem
  state as `status` and never takes the experiment lock.
- New CLI commands: `roastmyharness tool watch <id>` (NDJSON) and
  `roastmyharness watch <id>` (live ASCII matrix).
- The Pi extension now defaults `start` to watching. The tool card streams
  live per-trial progress in place with a progress bar, per-variant counts,
  active cells, and recent trials. The configured tool-expand key shows the
  full matrix. The final card includes aggregates and report paths. Aborting
  detaches the watch only; the run continues.
- New tool params: `watch` (default true), `interval_sec`, and `recent`.
  New actions: `author` and `watch`. `prepare` summaries now include the
  experiment name, Pi version, exact control reuse policy, and normalized
  variant sources. `status` responses now include per-variant aggregates.
- Per-trial stats on the live watch card. Each `trial` event now carries a
  `stats` block from `trial_row`: input/output/cache tokens, tool calls,
  turns, and wall time. Trials whose agent never finished carry no stats.
  The extension appends one aligned table per completed (task, variant) to
  the same card. The summary grows as the run progresses and stays for the
  final aggregates. Trials completed before the watch started are not
  backfilled.

Simplification pass (complexity audit 20260826184821-a3fbe704, Tiers 0-1).

### Fixed

- Home staging now normalizes extension and skill names derived from
  dot-directory sources (`~/.pi-git-suite` stages as `pi-git-suite`).
  Pi rejects leading-dot extension names, which failed home building
  after the wizard accepted the spec.

- The Pi wizard supplies verified metadata for configured local Pi packages to
  its isolated authoring context. The author can inspect other sources with
  read-only tools and cannot launch a benchmark. This prevents guesses about
  local paths or npm package versions while keeping authoring work out of the
  main session context.
- CI now runs locked dependency checks, Ruff, tests with a 70% coverage gate,
  and a wheel build.
- Run, resume, and report now use an exclusive per-experiment lock, while
  status observes existing jobs without rebuilding homes or changing state.
- SIGINT and SIGTERM share the graceful cancellation path, with a portable
  fallback for event loops that do not support `add_signal_handler`.
- Reconciled filesystem attempts update their existing trial row instead of
  creating duplicates on every poll or finalization.
- Auth-file updates and report artifacts use atomic replacement, and run
  diagnostics are structured JSONL with credential redaction.
- Run-artifact secret scanning covers every regular text-like artifact rather
  than log files only.
- Shell-sensitive setup inputs now require safe paths or exact package/version
  pins before they reach in-container root commands.
- `PiAgent.network_allowlist` no longer raises `NameError` for the default
  `openai-codex` provider (regression in the host-model commit that removed
  `_BUILTIN_PI_PROVIDERS`); covered by new adapter tests.
- Trial reconciliation no longer overwrites a `verifier/reward.json`
  fallback reward with 0.0. Cells and control observations now record the
  true reward for such trials.

### Removed (breaking for experiment ids)

Removed spec fields change `spec_hash` for every existing TOML, so re-running
`roastmyharness run` on an unchanged TOML now creates a NEW experiment id.
Existing runs remain resumable (`roastmyharness resume <old-id>` loads the
stored spec). Spec parsing is strict: unknown keys are rejected.

- `concurrency.global_max` (never enforced by any runtime code)
- `output.budget_usd` and the whole `[output]` section (the budget check was
  advisory-only: it printed a warning event that nothing consumed)
- `[model] auth` (decorative: the provider name alone drives credential
  staging; `auth = "api_key"` with `provider = "openai-codex"` now fails at
  staging instead of spec validation)
- the `extra_flags_json` adapter kwarg (never sent by the runner; use the
  wired per-variant `pi_flags` spec field instead). If you drove the adapter
  directly via pier `--ak extra_flags_json=...`, migrate to `pi_flags`.

### Removed (internal, no spec impact)

- The agent skill and its implicit prose workflow. Pi setup now installs only
  the slash-command extension. Claude setup now installs only the MCP server.
- Typed event dataclasses; the controller now takes `progress` and `ask`
  callbacks. `run`/`resume` still print per-trial progress to stderr
  (`[roast] <variant>/<task>: <status>`).
- Dead declarations: duplicate `Cell`, `staging_note`, `control_pools`,
  `homes_for_experiment`, `Repository.trials`, `binomial_two_sided`,
  unused error classes, `HomeBuild.cached`, unused `build_run_args`
  parameters (`agent_env`, `extra_args`), `tomlkit` dependency,
  `config_file`/`config_dir` helpers (no code ever read a config file).
