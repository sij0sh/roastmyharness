# Changelog

## Unreleased

### Added

- The Pi integration now provides the user-invoked `/roast` command and a
  `/roastmyharness` alias.
- The linear wizard collects variants, control mode, model, and task count.
  It generates and validates YAML. It repeats a `Confirm and run` or `Change`
  review loop before launch.
- Experiment specs can now use YAML (`.yaml` or `.yml`) as well as TOML.
- Live progress streaming for `roast_harness`. `AgentService.watch` emits
  read-only NDJSON events: snapshot, trial, state, heartbeat, and a final
  event with aggregates and report paths. It polls the same filesystem
  state as `status` and never takes the experiment lock.
- New CLI commands: `roastmyharness tool watch <id>` (NDJSON) and
  `roastmyharness watch <id>` (live ASCII matrix).
- The Pi extension now defaults `start` to watching. The tool call streams
  live per-trial progress in the TUI: collapsed counts plus recent trials,
  Ctrl+O expands the full matrix. It returns final aggregates and report
  paths. Aborting detaches the watch only; the run continues.
- New tool params: `watch` (default true), `interval_sec`, and `recent`.
  New action: `watch`. `status` responses now include per-variant
  aggregates.

Simplification pass (complexity audit 20260826184821-a3fbe704, Tiers 0-1).

### Fixed

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
stored spec) and old TOMLs still load (unknown keys are ignored).

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
