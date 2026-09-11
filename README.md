# RoastMyHarness

> **Your coding model is not the whole system. Benchmark the rest of it.**

RoastMyHarness is an experimental benchmarking harness for testing whether the things you add around a coding agent actually make it better.

Extensions. Skills. Extra tools. Context management. Retrieval. Alternate harnesses. Bigger system prompts.

They all sound useful.

They also change the system the model has to operate inside.

**Before you give your agent another tool, make it earn its tokens.**

RoastMyHarness runs the same coding tasks with the same model and thinking level against a stripped-down **Pi control** and one or more harness variants, then compares what actually happened:

* Did it solve more tasks?
* Which tasks did it rescue?
* Which tasks did it break?
* Did it use more tokens to get there?
* Did it take longer?
* Did it make more tool calls or turns?
* Did extra context actually reduce context pressure?
* Was the improvement large enough to justify the added machinery?

The goal is not to find the harness with the most features.

The goal is to use data to build harnesses that help.

> **MVP / WIP:** RoastMyHarness is under active development. The current focus is Pi as the control, Pi and Pi-family harness variants, and the DeepSWE benchmark. Broader agent and benchmark support is planned.

---

## The uncomfortable result so far

Base Pi is annoyingly hard to beat.

In the experiments run with RoastMyHarness so far, relatively few additions clearly outperform the stripped Pi control. Adding tools, skills, context, or harness behavior often produces one of two outcomes:

1. the benchmark score does not improve, but the run gets longer and uses more tokens, or
2. the added machinery actively lowers the benchmark score.

That is not a claim that tools, skills, or context engineering are useless. It is exactly why this project exists.

A feature can be excellent on the task it was designed for and still be a net regression across a broader workload. A tool can rescue five tasks while breaking seven others. A context system can reduce one kind of token use while causing the agent to take more turns. A skill can provide valuable instructions while also distracting the model on tasks where those instructions do not apply.

Without a control, all of those can *feel* like improvements.

RoastMyHarness is intended to make them measurable.

---

## Why benchmark the harness?

Most model benchmarks hold the surrounding system relatively constant and ask:

> Which model performs better?

When building coding agents, there is another question that matters just as much:

> **Did the system I built around the model make it better or worse?**

A modern coding harness can affect nearly every part of a run:

| Change                    | What you hope happens     | What might actually happen             |
| ------------------------- | ------------------------- | -------------------------------------- |
| Add a skill               | Better domain behavior    | More irrelevant instructions           |
| Add a tool                | New capability            | More tool-selection overhead           |
| Add retrieval             | Better information        | Larger prompts and distraction         |
| Add context management    | Longer effective sessions | Extra calls, rewrites, or lost context |
| Add repository metadata   | Better navigation         | More tokens before useful work starts  |
| Add orchestration         | Better decisions          | More turns and latency                 |
| Add another harness layer | Smarter workflow          | More opportunities to go wrong         |

The only reliable answer is to run the same work both ways.

That is the experiment RoastMyHarness is built around.

---

## How it works

A RoastMyHarness experiment contains:

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

For Pi, the control deliberately removes implicit harness features:

```text
-nc --no-skills --no-prompt-templates --no-themes
```

Variants can then introduce the thing you actually want to measure:

* local Pi extensions
* pinned npm extensions
* skills
* environment changes
* typed setup steps
* alternate supported agents or Pi-family harnesses
* combinations of the above

Every arm runs against the same selected tasks, model, and thinking level.

RoastMyHarness collects benchmark outcomes alongside telemetry such as input/output/cache tokens, cost when available, peak context, Pi compactions, turns, tool calls, wall time, and other run-level behavior.

The point is not merely to produce a leaderboard.

It is to answer **what changed, where it changed, and what it cost.**

---

## Why DeepSWE?

RoastMyHarness currently bundles DeepSWE as its primary benchmark.

That choice is deliberate.

Short coding tasks can badly underrepresent the effects of a harness. Many harness features are specifically intended to help with repository exploration, extended reasoning, context pressure, repeated tool use, or longer implementation work. If the benchmark finishes in a handful of turns, you may mostly be measuring startup overhead.

DeepSWE uses substantially longer software-engineering tasks, which gives harness behavior more room to matter.

It is still only an approximation.

### What DeepSWE improves

Longer tasks provide more opportunity to observe:

* repository exploration
* repeated reads and edits
* tool-selection behavior
* token accumulation
* longer reasoning trajectories
* context-management effects
* whether extra capabilities eventually pay for their overhead

### What DeepSWE still does not reproduce

A DeepSWE run is **not** the same thing as a real long-running coding relationship with an agent.

In particular:

* there is no user answering questions mid-task
* there is no back-and-forth clarification over multiple sessions
* there is no developer redirecting the agent after seeing partial work
* there is no persistent relationship spanning hours or days
* runs rarely reach a Pi native context-compaction phase

That last point matters.

Many harness ideas are designed specifically for very long contexts. DeepSWE gets closer to that regime than short benchmarks, but it generally does not push the agent through the same lifecycle as a genuinely long interactive coding session.

So the benchmark should be interpreted for what it is:

> **A controlled signal about coding-task performance, not a complete simulation of how a human and agent work together.**

Closing that gap is part of the broader direction for the project.

---

## Why Luna High?

GPT-5.6 Luna at High thinking is frequently used as the reference configuration for RoastMyHarness experiments because its roughly **44% benchmark score** sits in a useful measurement range.

That is important.

If the control solves 95% of the benchmark, there is almost no room for a new harness to demonstrate improvement.

If the control solves 5%, there are very few successful behaviors for a harness to preserve, and failures from both configurations tend to collapse together.

Around 44%, there is useful room in both directions:

```text
0%                 ~44%                            100%
│───────────────────●────────────────────────────────│
        room to regress         room to improve
```

A harness can rescue tasks the control fails **and** break tasks the control succeeds on.

Both signals matter.

### The curated Luna High suites

Running the full benchmark every time is expensive, so RoastMyHarness includes model-specific curated task sets.

For Luna High, the first **30 tasks** are intended as a signal screen. They emphasize frontier tasks with historical rollout outcomes between roughly 25% and 75%, where changes in harness behavior have a reasonable chance of becoming visible.

The **60-task suite** adds another 30 tasks for confirmation, including regression and floor anchors.

Think of them as:

```text
1 task       smoke test
30 tasks     signal screen
60 tasks     signal + confirmation
full suite   broader benchmark
```

The curated suite does not magically make 30 runs statistically definitive. Small samples still require careful interpretation.

It is designed to spend benchmark compute where differences are more likely to be informative.

A curated GLM-5.3-Flash Max suite is also included.

---

## Read the flips, not just the score

Suppose a variant scores 47% and the control scores 43%.

Good result?

Maybe.

The aggregate score hides the most useful question:

> **Were they failing on the same tasks?**

RoastMyHarness reports paired outcomes so you can see tasks where the variant changed the result.

For example:

```text
Control fail → Variant pass    RESCUED
Control pass → Variant fail    BROKEN
Control pass → Variant pass    BOTH PASS
Control fail → Variant fail    BOTH FAIL
```

If a variant rescues eight tasks and breaks one, that is interesting.

If it rescues five and breaks five, a four-point aggregate difference may not mean what you think it means.

At small task counts, **paired flips are usually more useful than staring at the topline percentage.**

---

## Quality is only half the result

A harness change does not need to use fewer tokens to be worthwhile.

It does need to earn the tokens it adds.

A variant that improves solve rate substantially while using 15% more compute may be an excellent trade.

A variant that adds 40% more tokens, takes longer, makes more tool calls, and solves fewer tasks probably needs another trip to the drawing board.

RoastMyHarness reports both sides of that tradeoff.

Typical measurements include:

* benchmark reward / resolved result
* input tokens
* output tokens
* cache-read tokens
* reasoning tokens
* peak context
* Pi native compactions
* LLM calls
* agent turns
* tool calls
* file-read behavior
* wall-clock time
* reported model cost

Do not optimize any one number blindly.

The useful question is usually:

> **What quality did I gain or lose for the additional complexity and compute?**

---

# Quick start

## Requirements

RoastMyHarness currently expects:

* Python 3.12+
* [`uv`](https://docs.astral.sh/uv/)
* Pier 0.3.x
* Docker or a Docker-compatible runtime
* Pi authentication for the model/provider you intend to use

Install Pier:

```bash
uv tool install "datacurve-pier>=0.3,<0.4"
```

Install RoastMyHarness from the repository:

```bash
uv tool install .
```

Verify the CLI:

```bash
roastmyharness --help
```

---

## Authentication

For the default `openai-codex` provider, RoastMyHarness reuses Pi's existing Codex OAuth credentials.

Authenticate through Pi:

```text
pi
/login codex
```

Then verify:

```bash
roastmyharness auth status
```

RoastMyHarness does not maintain a separate credential store. Credentials are staged for individual jobs rather than stored in cached benchmark homes.

---

## Install the Pi integration

Install the `/roastmyharness` command:

```bash
roastmyharness setup --agent pi --scope user
```

Then check the environment:

```bash
roastmyharness doctor
```

`doctor` checks Pi, Pier, Docker, authentication, models, and integration health.

---

# The easiest way to run an experiment

From Pi:

```text
/roastmyharness
```

The wizard walks through:

1. what harness variants you want to compare
2. whether to run a fresh Pi control or use eligible historic controls
3. the model
4. the thinking level
5. the curated benchmark suite
6. how many tasks to run

You can also start by describing what you want:

```text
/roastmyharness compare my local context extension against base Pi
```

The integration authors a TOML experiment spec, validates the sources and environment, shows the resulting plan, and lets you confirm before launch.

During the run you get live progress, per-variant results, trial telemetry, and the final report locations without tying up the main Pi prompt.

---

# CLI workflow

You can run everything without the Pi integration.

Create a starter experiment:

```bash
roastmyharness init my-experiment.toml
```

Edit it:

```bash
$EDITOR my-experiment.toml
```

Validate the experiment and environment:

```bash
roastmyharness validate my-experiment.toml
```

Run it:

```bash
roastmyharness run my-experiment.toml
```

List experiments:

```bash
roastmyharness list
```

Inspect one:

```bash
roastmyharness status <experiment-id>
```

Watch it live:

```bash
roastmyharness watch <experiment-id>
```

Regenerate the reports:

```bash
roastmyharness report <experiment-id>
```

Interrupted experiments are resumable:

```bash
roastmyharness resume <experiment-id>
```

By default, resume runs only missing cells. You can also target individual tasks or variants and retry infrastructure errors:

```bash
roastmyharness resume <experiment-id> \
  --task some-task \
  --variant my-extension \
  --retry-errors
```

---

# Experiment specs

Experiments are declarative TOML.

A minimal extension comparison looks like this:

```toml
schema_version = 1
name = "my-extension-test"

# Use "latest" while iterating, or pin a version for reproducible comparisons.
pi_version = "latest"
thinking = "high"

[model]
id = "gpt-5.6-luna"
provider = "openai-codex"

[tasks]
path = "/path/to/task-dataset"
include = ["*"]
exclude = []

[concurrency]
per_variant = 2

[control]
enabled = true
reuse = "never"

[[variants]]
id = "my-ext"
name = "My extension"

[[variants.extensions]]
kind = "local"
path = "~/my-extensions/my-extension"
entry = "src/index.ts"
```

The experiment above asks a simple question:

```text
base Pi
   vs
base Pi + ~/my-extensions/my-extension
```

Same tasks. Same model. Same thinking level.

That is the comparison.

See the included examples for more configurations:

* [`examples/local-extension.toml`](examples/local-extension.toml)
* [`examples/npm-contextmode.toml`](examples/npm-contextmode.toml)
* [`examples/omp-variant.toml`](examples/omp-variant.toml)
* [`examples/cross-agent.toml`](examples/cross-agent.toml)

---

# What can a variant change?

Variants currently support things such as:

### Local extensions

```toml
[[variants.extensions]]
kind = "local"
path = "~/my-extensions/my-extension"
entry = "src/index.ts"
```

### Pinned npm extensions

```toml
[[variants.extensions]]
kind = "npm"
package = "some-extension@1.2.3"
```

Exact pins are preferred because a benchmark comparison is not very useful if the thing being benchmarked changes underneath it.

### Skills

```toml
[[variants.skills]]
kind = "local"
path = "~/my-skills/my-skill"
```

The skill directory must contain `SKILL.md`.

Variants can also define environment values and supported setup handlers.

---

# Fresh vs historic controls

Controls run fresh by default:

```toml
[control]
enabled = true
reuse = "never"
```

For repeated experimentation, rerunning the exact same control can become expensive.

RoastMyHarness therefore supports opt-in historic control reuse:

```toml
[control]
enabled = true
reuse = "ask"       # never | ask | require

minimum_runs_per_task = 10
maximum_age_days = 30
sentinel_tasks = 6
```

Historic results are only useful when the underlying experiment is still comparable. Reuse is tied to things such as the resolved agent version, model, thinking level, control configuration, and task.

Fresh sentinel tasks are used to check for drift before accepting eligible historical observations.

Reports disclose reused control counts, ages, and the sentinel verdict. Reused cells are also visibly distinguished from fresh results.

Historic controls are **not** contemporaneous paired observations. Treat them accordingly.

---

# Reproducibility

By default:

```toml
pi_version = "latest"
```

This is useful when the question is:

> How does my harness compare with the Pi someone would install today?

It is not ideal when the question is:

> Did my extension improve between commit A and commit B?

For controlled longitudinal experiments, pin Pi:

```toml
pi_version = "0.x.y"
```

RoastMyHarness records the resolved versions used by the experiment, and source/task identity is incorporated into experiment planning so changed inputs do not silently masquerade as the same experiment.

The general rule is simple:

> **Float versions when testing current reality. Pin versions when testing your own changes.**

---

# Reports

Completed runs automatically produce:

```text
summary.csv
summary.json
report.md
```

`report.md` includes:

* completion and error counts
* resolve rates
* bootstrap confidence intervals
* average token use
* average reported cost
* average wall time
* paired task flips
* control-reuse provenance when applicable
* interpretation notes

`summary.csv` contains the lower-level per-trial data for your own analysis.

The CSV schema is currently tool-owned and may change while RoastMyHarness is an MVP.

---

# What should I benchmark?

Small, isolated changes are easiest to interpret.

Good experiment:

```text
control
vs
control + one retrieval extension
```

Harder to interpret:

```text
control
vs
new harness + six skills + three MCP servers + custom prompt +
context compression + different agent + different model
```

The second configuration may win, but you will have very little evidence about **why**.

Treat harness work like performance engineering:

1. form a hypothesis
2. change one meaningful thing
3. benchmark it
4. inspect the rescued and broken tasks
5. inspect the cost
6. keep, revise, or remove the change

Then repeat.

RoastMyHarness exists to make that loop cheap enough to actually use.

---

# Current scope

RoastMyHarness is intentionally narrow today.

The center of gravity is:

```text
Pi control
    ↓
Pi extensions / skills / harness changes
    ↓
supported Pi-family alternatives
    ↓
DeepSWE
```

The current agent registry includes Pi and OMP / oh-my-pi.

This is not yet intended to be a universal agent leaderboard or a definitive benchmark of every coding workflow.

That narrower scope is useful during the MVP stage because it makes the control meaningful. The more dimensions that change simultaneously, the harder it becomes to attribute a result to the harness.

---

# Known limitations

RoastMyHarness produces data. It does not make that data more universal than it is.

Keep the following in mind:

### DeepSWE is not a real developer session

The tasks are longer than many coding benchmarks, but they still do not reproduce repeated human interaction, changing requirements, multiple sessions, or the full context lifecycle of long-running coding work.

### Context-management systems may be under-tested

DeepSWE runs rarely hit Pi's native compaction phase. A feature specifically designed for extremely long contexts may therefore provide value that this benchmark does not fully exercise.

### Small suites are noisy

The curated 30-task suite is a signal screen, not proof. Use paired flips and follow promising results with a larger run.

### Token reductions are not automatically improvements

Using fewer tokens while solving fewer problems is not efficiency.

Likewise, using more tokens is not automatically bad if the additional compute buys a meaningful quality improvement.

### Cost telemetry depends on the provider

If a gateway does not report per-call cost, cost fields may be zero or unavailable. Token counts remain the more portable measurement.

### Historic controls are not fresh pairs

Control reuse saves compute, but reused observations were not generated at exactly the same time as the variant. RoastMyHarness exposes this provenance rather than pretending otherwise.

### It is an MVP

Interfaces, report schemas, supported agents, and benchmark behavior are still evolving.

Expect some sharp edges.

---

# Where this is going

The immediate goal is not more features for their own sake.

It is better experimental coverage.

Areas for expansion include:

* additional coding-agent adapters beyond the current Pi-family focus
* benchmarks beyond Pier / DeepSWE
* workloads that better represent very long and multi-session coding
* richer analysis of quality-versus-compute tradeoffs
* remote execution and larger experiment fan-out
* better visualization and experiment comparison

The long-term question remains the same:

> **Does this harness change make the agent meaningfully better?**

If the answer is yes, RoastMyHarness should help show where and by how much.

If the answer is no, finding that out before adding another thousand lines of orchestration is also a win.

---

# Project status

RoastMyHarness is currently an **MVP / work in progress**.

Core experiment execution, telemetry, reporting, resume behavior, DeepSWE integration, Pi integration, and historic-control reuse are functional.

Expect active iteration around benchmark coverage, agent support, analysis, and ergonomics.

Issues, benchmark findings, and especially reproducible examples of harness changes that unexpectedly help or hurt are useful input.

---

# Uninstall

```bash
uv tool uninstall roastmyharness
```

Run data and cached homes can be removed separately:

```bash
rm -rf ~/.local/share/roastmyharness
rm -rf ~/.cache/roastmyharness
```

RoastMyHarness does not own Pi's authentication file and does not remove it.

Run outputs live under the platform data directory unless `ROAST_MY_HARNESS_RUNS_DIR` is set.

---

# License

MIT. See [`LICENSE`](LICENSE).

---

## Benchmark your clever idea before shipping it

The easiest harness feature to justify is the one that obviously *should* help.

Those are also the ones worth measuring.

```text
Build it.
Run it against the control.
Look at what it rescued.
Look at what it broke.
Look at what it cost.
Then decide.
```

**Roast your harness before your harness roasts your code.**
