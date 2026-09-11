# RoastMyHarness

> **Your coding model is not the whole system. Benchmark the rest of it.**

RoastMyHarness is a Pi-native experiment extension. It compares a stripped Pi baseline against one or more explicitly configured Pi variants, using the same task, model, and thinking level.

Extensions. Skills. AGENTS.md. Settings. Pi versions.

They all sound useful. They also change the system the model operates inside.

**Before you give your agent another tool, make it earn its tokens.**

Each experiment runs the same coding tasks with the same model and thinking level against a bare-Pi **control** and your changed-Pi **variants**, then compares what actually happened:

* Did it solve more tasks?
* Which tasks did it rescue?
* Which tasks did it break?
* Did it use more tokens to get there?
* Did it take longer?
* Was the improvement large enough to justify the added machinery?

The goal is not to find the configuration with the most features. The goal is to use data to build Pi setups that help.

> **MVP:** RoastMyHarness is under active development. It runs Pi only, on the bundled DeepSWE benchmark or a compatible external task root.

---

## The uncomfortable result so far

Base Pi is annoyingly hard to beat.

In the experiments run so far, relatively few additions clearly outperform the stripped Pi control. Adding extensions, skills, or context often produces one of two outcomes:

1. the benchmark score does not improve, but the run gets longer and uses more tokens, or
2. the added machinery actively lowers the benchmark score.

A feature can be excellent on the task it was designed for and still be a net regression across a broader workload. A tool can rescue five tasks while breaking seven others.

Without a control, all of those can *feel* like improvements. RoastMyHarness makes them measurable.

---

## How it works

```text
                    same task
                    same model
                 same thinking level
                         │
              ┌──────────┴──────────┐
              │                     │
         CONTROL                VARIANT
         base Pi              your changes
              │                     │
              └──────────┬──────────┘
                         │
                 compare outcomes
```

The control is always fresh and implicit: bare Pi, run alongside every variant. There is no control configuration. Every experiment asks one question:

> Did this Pi configuration make Pi better?

RoastMyHarness collects benchmark outcomes alongside telemetry such as input/output/cache tokens, cost when available, turns, tool calls, and wall time.

The point is not a leaderboard. It is to answer **what changed, where it changed, and what it cost.**

---

## The one workflow

There is exactly one way to use RoastMyHarness: the Pi extension.

```text
install
→ roastmyharness setup
→ pi
→ /roastmyharness
→ wizard asks what you are testing
→ your Pi session writes the experiment TOML
→ extension validates it and shows a review card
→ Run
→ live run card with progress and trial results
→ final summary card
→ your Pi session analyzes the results
```

RoastMyHarness is invisible until invoked: no prompt text and no model tools exist before `/roastmyharness`. During the wizard exactly one temporary handoff tool exists, and only so the session can hand its finished TOML back for validation.

### Requirements

* Python 3.12+
* [`uv`](https://docs.astral.sh/uv/)
* Pier 0.3.x (`uv tool install "datacurve-pier>=0.3,<0.4"`)
* Docker or a Docker-compatible runtime
* Pi authentication for the model you intend to use

### Install

Pi owns the extension. Uv owns the engine. Install both.

```bash
pi install git:github.com/sij0sh/roastmyharness@v0.1.0
uv tool install --from git+https://github.com/sij0sh/roastmyharness roastmyharness
roastmyharness doctor
```

From a local checkout, `uv tool install .` plus `roastmyharness setup`
copies the extension instead. When Pi already manages the extension,
`setup` defers to Pi and changes nothing.

### Updates

```bash
pi update --extensions   # extension reconcile; Pi notifies when due
uv tool upgrade roastmyharness --from git+https://github.com/sij0sh/roastmyharness
```

Git tags use `vX.Y.Z` and match the engine version. To move to a new
release, reinstall the Pi package at the new tag, then upgrade the uv
tool. The extension checks the engine on every session start. A missing
engine shows an error. A version skew shows a warning with the exact
upgrade command. `/roastmyharness` stays blocked until they match.

For the default `openai-codex` provider, authenticate through Pi (`/login codex`). RoastMyHarness reuses Pi's existing credentials and keeps no credential store of its own. `doctor` checks Pi, Pier, Docker, authentication, models, and extension health in one place.

The `roastmyharness` binary otherwise stays out of your way. Besides `setup` and `doctor` it exposes only a private `_bridge` protocol the extension uses internally.

---

## Experiment specs

Specs are `schema_version = 3` TOML. Your Pi session writes them; the extension validates them.

```toml
schema_version = 3
name = "test-my-context-extension"

pi_version = "latest"
model = "openai-codex/gpt-5.6-luna"
thinking = "high"

[tasks]
path = "tasks/deepswe/tasks"
preset = "luna-signal"

[[variants]]
id = "context-extension"
agents_md = "../my-extension/AGENTS.md"

[[variants.extensions]]
path = "../my-extension"
entry = "src/index.ts"
```

One model per experiment, shared by all arms, resolved against your Pi `models.json` inventory. That keeps the experiment from accidentally answering "is model B better?" when the question is "did extension B help?"

A variant can change:

* `extensions` — local paths or pinned `package@x.y.z` npm packages
* `skills` — local skill directories
* `agents_md` — one instruction file, staged as a real `AGENTS.md` and discovered through Pi's native semantics
* `settings` — a JSON file merged into the staged `settings.json`
* `pi_version` — optional per-variant override, for comparing Pi versions themselves
* `env` / `env_from_host`, `egress_urls`, `pi_flags` — runtime Pi configuration

The control is never declared. It is always bare Pi at the experiment's `pi_version`.

---

## Read the flips, not just the score

At small task counts, resolve-rate differences alone are not signal. The report centers on paired flips: tasks the variant rescued versus tasks it broke, with per-task detail.

Quality is only half the result. Token, cost, and wall-time columns sit next to every score so overhead reads as part of the verdict, not as a footnote.

---

## Why DeepSWE?

Short coding tasks underrepresent harness effects. Many Pi features exist to help with repository exploration, extended reasoning, context pressure, or repeated tool use — none of which matter when a benchmark finishes in a handful of turns.

DeepSWE uses substantially longer software-engineering tasks, which gives harness behavior room to matter. It is still only an approximation: no user answers questions mid-task, and runs rarely reach Pi's native context-compaction phase.

> **A controlled signal about coding-task performance, not a complete simulation of how a human and agent work together.**

GPT-5.6 Luna at High thinking (~44% baseline) is a useful reference configuration: with room to improve and room to regress, a variant can rescue tasks the control fails *and* break tasks it succeeds on. Both signals matter.

---

## Current scope

* Pi only. No other agents, no MCP server, no standalone benchmark CLI.
* Bundled DeepSWE plus compatible external task roots. No custom-eval authoring.
* Fresh implicit control on every run. No historic baselines.
* Linux and macOS fully; Windows host support for setup, locking, process control, and the engine bridge.

## Known limitations

* Small suites are noisy; prefer the 30-task signal screen before trusting a result, and the 60-task suite for confirmation.
* Token reductions are not automatically improvements; check flips first.
* Cost telemetry depends on the provider reporting per-call costs.

---

## Configuration

All state lives under one home directory. Everything here is optional;
defaults keep 3GB of variant runs plus 5GB of control data.

| Setting | Default | Effect |
|---|---|---|
| `ROAST_MY_HARNESS_DATA_DIR` | `~/.roastmyharness` | Home for database, runs, plans, and `config.toml` |
| `ROAST_MY_HARNESS_RUNS_DIR` | `<home>/runs` | Where run outputs go |
| `ROAST_MY_HARNESS_CACHE_DIR` | `<home>/cache` | Content-addressed home-image cache |
| `ROAST_PROBE_TIMEOUT` | `1800` | Seconds before the pre-run smoke probe gives up |
| `ROAST_MY_HARNESS_DEBUG` | unset | Verbose CLI error output when set |
| `ROAST_MY_HARNESS_REPO` | unset | Repo checkout path used by `setup` |

`config.toml` (in the home directory) fine-tunes storage and retention.
Environment variables override every value below.

```toml
[storage]
runs_dir = "~/benchmark-runs"   # else <home>/runs

[retention]
enabled = true                 # false keeps everything
variant_max_size = "3GB"      # cap on non-control run data
control_max_size = "5GB"      # cap on control-arm data
control_keep = 4              # resolved control trials kept per task/model/thinking
control_retention = false     # true lifts the control size cap, keeps the per-group prune
```

Legacy `max_size` still works and feeds the variant cap. Size values
accept bytes or `MB`/`GB` suffixes. When `control_keep` is exceeded for
a task/model/thinking combo, the oldest control trials are pruned first.
Size enforcement deletes oldest whole runs first and never the active run.

## Uninstall

```bash
uv tool uninstall roastmyharness
rm -rf ~/.roastmyharness
```

## License

MIT. See [LICENSE](LICENSE).
