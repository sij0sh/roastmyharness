# Changelog

## Unreleased — Pi-only refactor (schema v3)

RoastMyHarness is now a Pi-native experiment extension. The spec is
`schema_version = 3`, and every run compares a fresh implicit bare-Pi
control against explicitly configured Pi variants unless the spec sets
`control = false`.

### Added

- Six-step `/roastmyharness` wizard. Step 1 takes a freeform variant
description. Step 2 picks the model from the Pi inventory with Luna,
Muse Spark, and GLM Flash pinned. Step 3 picks the thinking level
with per-model defaults. Step 4 picks the control mode. The wizard
offers historic only when control data exists for the model/thinking
combo. Step 5 picks the task set: smoke, Luna/GLM curated 30/60,
full suite, custom, or the historic pool with curated hybrid fill.
Step 6 picks repetitions 1-4. Pi infers names, paths, and variant
configuration from the freeform answer and writes the TOML.
- Top-level `control = false` spec field (default true) for
variant-only runs. Historic mode uses past control trials as the
baseline instead of a fresh arm.
- Private `_bridge wizard-context` command. It reports discovered
tasks, curated suites filtered to disk, and the historic control pool
for one model/thinking combo.
- Split retention for control data. `[retention]` gains `variant_max_size`
(default 3GB), `control_max_size` (default 5GB), `control_keep` (default
4, resolved control trials kept per task/model/thinking), and
`control_retention` (default false; true lifts the control size cap and
keeps only the per-group prune). Legacy `max_size` still feeds the
variant cap. `enabled = false` keeps everything, as before.
- Near-miss test columns in the tool-owned schema. `summary.csv` gains
`f2p_total`, `f2p_passed`, `p2p_total`, `p2p_passed`, `tests_total`,
`tests_passed`, and `partial`, promoted from the verifier rewards map.
`summary.json` embeds a byte-reproducible `charts` series, `report.md`
gains Near misses and Charts sections, and `analysis.md` reports mean
partial plus near-miss counts per arm.
- PNG chart pipeline. Finalize renders `charts/` (`resolve-rate`,
`flips`, `near-miss`, `partial-delta`, `cost`) via matplotlib, and
`roastmyharness charts <run>` regenerates them. The Pi extension gains
an always-available `show_roast_charts` tool that renders the PNGs with
the native `Image` component and keeps bytes out of model context.

### Removed

- Claude and OMP agents, the agent registry abstraction, and all
  cross-agent examples, goldens, and credential paths. One adapter
  remains: `PiAgent`.
- The standalone MCP server and Claude setup. `setup` installs the Pi
  extension only, by copying files (no symlinks).
- The public standalone run workflow (`init`, `validate`, `run`,
  `resume`, `status`, `watch`, `list`, `report`, `tool ...`, `eval ...`).
  The binary exposes `setup`, `doctor`, a private `_bridge`
  (`inspect` / `validate` / `run` / `status` / `cancel`), and a private
  `_worker`.
- Historic-control reuse: `ControlSpec`, the `[control]` block,
  sentinel/drift policy, `history-availability`, and the
  `control_observations` store API. Every control runs fresh.
- Custom-eval authoring (`eval init`, scaffolding, fixtures, critic,
  self-tests) and the `generated` eval type. Supported: bundled DeepSWE
  and compatible external task roots.
- Specialized setup handlers (arbitrary binaries, RTK, Codegraph,
  Snoop). Variants declare Pi configuration; npm extensions install
  through one generic installer.
- `context_files` and its prompt-injection delivery. Variants declare
  `agents_md`, staged as a real `AGENTS.md` discovered through Pi's
  native semantics. The `-nc` fairness override is gone.
- Generic-agent vocabulary: `agent`, `agent_version`,
  `agent_versions`, per-variant models, `resolved_agents()`,
  `model_for()`. Per-variant `pi_version` overrides remain, frozen per
  run as `resolved_pi_versions` (resolved envelope v2).
- The isolated spec-author child session and persistent model tools.
  The wizard collects facts, the current Pi session writes the TOML,
  and one temporary `submit_roast_experiment` tool validates and
  launches it with a live run card.

### Changed

- Experiment specs accept `model = "provider/model"`, inferred
  extension kinds (`path` vs `package`), string shorthand for skills,
  and `agents_md` / `settings` variant files.
- Home cache keys exclude literal env values; cached homes stay
  secret-free.
- Host locking, process control, and extension install are
  cross-platform (`host_lock`, `host_process`, copy-based install).
  The smoke-probe timeout path and the run-lock probe share them.
- Reports disclose a fresh control section; the historic disclosure
  section is gone. Variant types classify `agents_md` arms as
  `context_file`.

### Kept

- `PiAgent`, Pi telemetry, immutable staged homes, `models.json`
  inventory auth staging, Pier execution, reconciliation, presets,
  paired flips, measurements, machine-readable summaries, custom cards.
