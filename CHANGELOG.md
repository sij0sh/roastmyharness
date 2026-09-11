# Changelog

## Unreleased — Pi-only refactor (schema v3)

RoastMyHarness is now a Pi-native experiment extension. The spec is
`schema_version = 3`, and every run compares a fresh implicit bare-Pi
control against explicitly configured Pi variants.

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
