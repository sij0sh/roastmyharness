# RoastMyHarness

> **Make your harness prove it helped.**

It is easy to make a coding agent more complicated.

You add a repository-search extension because it found the right file immediately in one ugly codebase. You write a large `AGENTS.md` because the model keeps making the same mistake. You add a skill for debugging tests because it solved an overlooked problem, and a context manager because it kept useful observations that Pi's compact probably would have missed.

Any of those changes might be good. The problem is that they are surprisingly hard to judge by feel.

A new tool can be excellent when it is needed and still make the agent worse overall because the model now spends time deciding when to call it. Extra instructions can prevent one mistake while nudging unrelated tasks in the wrong direction.

RoastMyHarness exists to answer the less exciting but more useful question:

**When I put this thing around the model, did the system actually get better?**

It runs the same software-engineering tasks with the same model and thinking level against a plain Pi control and the Pi configuration you want to test. It then compares not just how many tasks were solved, but which tasks changed, how many tokens were used, how long the runs took, and what the agent did along the way.

---

## Why benchmark your harness?

When people talk about coding-agent performance, most of the attention goes to the model. In practice, the model is only part of what you are using.

The model also receives instructions, sees a particular set of tools, works under particular settings, and may have extensions or skills influencing how it searches, reads, edits, and manages context. That surrounding machinery is the **harness**.

The harness matters, but more machinery is not automatically more capability.

Imagine you add a fancy repository-search extension. On the first difficult task you try, it jumps directly to the relevant class instead of spending ten minutes searching through the repository. That is a compelling demo.

Now run it across thirty unrelated tasks.

Maybe the extension rescues three tasks that plain Pi could not solve. Good. But perhaps it also causes two previously passing tasks to fail, adds another tool call decision to nearly every run, and increases token use by 20 percent. You now have a much more interesting decision than “the search tool works.”

Maybe the extension is still worth keeping. Maybe it should only be exposed for certain repositories. Maybe its tool description needs work. Maybe it belongs in a hidden skill rather than in every session.

That is the sort of decision RoastMyHarness is meant to support.

---

## The mildly annoying result so far

Base Pi is hard to beat.

That has been one of the more useful findings from building and using RoastMyHarness. Relatively few additions tested so far have cleanly beaten the plain Pi control on both task performance and efficiency.

A common result is much less exciting: the modified harness solves roughly the same number of tasks, but gets there with more turns, more tokens, or more wall time.

Sometimes the result is worse. Something designed to help on a particular class of problems rescues a few failures but introduces new failures elsewhere.

That does not mean extensions, skills, better instructions, or context tools are pointless. It means their value is often narrower than a good demo suggests.

RoastMyHarness is intended to help find that boundary.

If a tool rescues the tasks it was built for without disturbing anything else, that is useful evidence. If it only helps when a repository is large, that is useful evidence too. If the best result is to leave it out of the default harness and enable it selectively, that is still a successful experiment.

The point is to use data to drive decisions as to how to improve your tools or when and how they get used.

---

## What an experiment actually does

A normal experiment has a plain Pi control and one or more variants.

The control uses Pi without the thing you are testing. A variant changes something: perhaps an extension, a skill, an `AGENTS.md`, a settings file, or even the Pi version itself.

Everything that should stay constant stays constant. The arms use the same coding tasks, model, and thinking level.

```text
                         same tasks
                         same model
                      same thinking level

                    ┌────────┴────────┐
                    │                 │
               bare Pi            changed Pi
               control             variant
                    │                 │
                    └────────┬────────┘
                             │
                    compare the runs
```


---

## Why DeepSWE?

A harness benchmark has an awkward problem: many harness features are specifically intended to matter during longer coding work.

A five-minute task may never put pressure on context. It may require almost no repository exploration. The agent may only use two or three tools before finding the answer. In that environment, a feature intended to improve a long coding session has little opportunity to show either its benefit or its cost.

RoastMyHarness currently uses DeepSWE because its software-engineering tasks are substantially longer than the tiny coding problems commonly used for quick evaluations. That gives the agent more opportunity to inspect a repository, make repeated tool calls, follow false leads, edit code, and generally behave enough like a coding agent for harness differences to become visible.

It is still not the same thing as real use.

A DeepSWE run does not have a developer answering questions halfway through the task. It does not reproduce a project that continues across many sessions. Runs rarely get far enough into Pi's context window to exercise native compaction in the way a truly long interactive session might.

So the claim is deliberately narrower:

**DeepSWE gives RoastMyHarness a useful controlled signal about longer autonomous coding tasks. It is not a simulation of an entire human-agent working relationship.**

That gap is one reason the project is still an MVP and why broader evaluation scope is on the development path.

---

## Tip: Use cheap models that hit 40-50% on DeepSWE

RoastMyHarness includes curated task sets for GPT-5.6 Luna at High thinking, and Luna High is often a useful place to start.

The shipped reference data puts Luna High at about **44%** on the underlying benchmark.

For harness work, that middling score is useful.

If your control model already solves 95 percent of the benchmark, there are very few failures left for a better harness to rescue. You can still measure regressions, but improvement is hard to see.

At the other extreme, if the model only solves 5 percent, there are almost no successful control tasks for a bad harness to break. You mostly learn that the model cannot do the benchmark.

---

# Getting started

## Requirements

You will need:

* Python 3.12 or newer
* `uv`
* Pier 0.3.x
* Docker or a Docker-compatible runtime
* Pi
* authentication in Pi for the model you want to benchmark

Install Pier if needed:

```bash
uv tool install "datacurve-pier>=0.3,<0.4"
```

For the default `openai-codex` provider, authenticate through Pi:

```text
/login codex
```

RoastMyHarness reuses Pi's existing authentication. It does not maintain a separate credential store.

---

## Install RoastMyHarness

There are two pieces.

Pi loads the extension that provides `/roastmyharness`. The Python package runs the experiment engine behind it.

For a tagged release:

```bash
pi install git:github.com/sij0sh/roastmyharness@v0.1.0

uv tool install \
  --from git+https://github.com/sij0sh/roastmyharness \
  roastmyharness

roastmyharness doctor
```

`doctor` is the easiest way to catch setup problems before spending time on a benchmark run. It checks the surrounding pieces RoastMyHarness depends on, including Pi, Pier, Docker, authentication, model availability, and extension health.

If you are working from a local checkout instead:

```bash
uv tool install .
roastmyharness setup
roastmyharness doctor
```

`setup` copies the local Pi extension into place. If Pi is already managing the extension as an installed package, setup leaves that installation alone.

---

## Run your first experiment

Roastmyharness comes with a wizard to make running experiments easy.

You type one command.

```text
/roastmyharness
```

The wizard asks six short questions, only one free formed. Pi itself then writes the config file and will run the analysis after completion.

The extension adds nothing to your everyday harness. It hides its two tools (`submit_roast_experiment` and `await_roast_experiment`) when the session starts. It exposes them only while the wizard runs. It rejects calls outside the wizard. Your normal Pi session keeps its tools, context, and behavior unchanged.

The walkthrough below uses a real run. It tests a third-party extension against bare Pi.

1. Describe the variant.

   ![Step 1 - describe the variant](docs/images/wizard-1-variant.png)

   This is the free form question. Describe whatever you want to test. Point to a local repo, a github url, a Pi global or local setting. 

2. Pick the model.

   ![Step 2 - pick the model](docs/images/wizard-2-model.png)

These come from your global Pi's models.json list.

3. Pick the thinking level.

   ![Step 3 - pick the thinking level](docs/images/wizard-3-thinking.png)

4. Pick the control.

   ![Step 4 - pick the control](docs/images/wizard-4-control.png)

You generally want to run against a control if you are looking to compare, however, you can also run solo for a quick smoke test or use historical control data if you built up enough runs on the model you are using.

5. Pick the tasks.

   ![Step 5 - pick the tasks](docs/images/wizard-5-tasks.png)

You can run the full benchmark, 1 as a smoke test, a custom amount or a curated set for Luna or GLM that were handpicked to give the most signal as possible from a smaller sample.

6. Pick the repetitions.

   ![Step 6 - pick repetitions](docs/images/wizard-6-repetitions.png)

DeepSWE gets its scores by running the benchmark 4 times and then averaging the results. It is useful for confirmation to run the same test more than once due to model variance. 

7. Review and launch.

   ![Review the generated TOML and launch](docs/images/wizard-7-review.png)

   Review the generated TOML to ensure the config looks right.

Pi will then run the tests and report back with a task, token, and wall time comparison. The logs are saved so it can be useful to ask Pi specific questions about the comparison such as if the variant worked as intended or had a direct cause in any differences.

---

# A few things not to over-interpret

Benchmarks are measurement tools, not oracles.

A change that wins on DeepSWE is not automatically better for every repository or every developer. A change that loses across the full benchmark may still be extremely valuable for the narrow class of work it was designed to handle.

Likewise, a thirty-task screen is deliberately small enough to be practical. Treat small differences as candidates for confirmation rather than proof.

Real interactive coding also contains behavior this benchmark does not reproduce well: developers answer questions, redirect the model, carry context across sessions, change requirements, and sometimes work long enough for context management to become one of the dominant problems.

RoastMyHarness is useful because it controls some of those variables, not because it has eliminated the difference between a benchmark and real work.

The best use of the data is to make a more precise claim.

Not:

> “This extension makes Pi better.”

But perhaps:

> “This extension appears to help on repository-heavy tasks, with a modest token penalty and no observed regression in the confirmation set.”

That is a claim you can build engineering decisions around.

---

# Configuration

RoastMyHarness keeps its state under a single home directory.

The defaults retain up to 3 GB of variant-run data and 5 GB of control data.

| Setting                      | Default             | Purpose                                      |
| ---------------------------- | ------------------- | -------------------------------------------- |
| `ROAST_MY_HARNESS_DATA_DIR`  | `~/.roastmyharness` | Database, run data, plans, and `config.toml` |
| `ROAST_MY_HARNESS_RUNS_DIR`  | `<home>/runs`       | Run output location                          |
| `ROAST_MY_HARNESS_CACHE_DIR` | `<home>/cache`      | Content-addressed staged-home cache          |
| `ROAST_PROBE_TIMEOUT`        | `1800`              | Pre-run smoke-probe timeout in seconds       |
| `ROAST_MY_HARNESS_DEBUG`     | unset               | Verbose CLI errors when enabled              |
| `ROAST_MY_HARNESS_REPO`      | unset               | Repository checkout used by local setup      |

More detailed storage and retention behavior can be configured in `<home>/config.toml`:

```toml
[storage]
runs_dir = "~/benchmark-runs"

[retention]
enabled = true
variant_max_size = "3GB"
control_max_size = "5GB"
control_keep = 4
control_retention = false
```

Environment variables override the file configuration.

When retention limits are reached, RoastMyHarness removes older run data rather than touching the active experiment. Control retention is tracked separately so repeated experiments do not require unlimited storage.

---

# Updating

The Pi extension and Python engine are versioned together.

Update the extension through Pi:

```bash
pi update --extensions
```

Update the engine with `uv`:

```bash
uv tool upgrade roastmyharness \
  --from git+https://github.com/sij0sh/roastmyharness
```

Release tags use `vX.Y.Z`.

The extension checks the engine version when a Pi session starts. If the engine is missing, `/roastmyharness` is blocked with an error. If the extension and engine versions do not match, it reports the mismatch and gives the upgrade command rather than silently running an experiment with an incompatible pair.

---

# Uninstall

Remove the Python engine:

```bash
uv tool uninstall roastmyharness
```

Remove local RoastMyHarness data if you no longer want the stored runs:

```bash
rm -rf ~/.roastmyharness
```

Remove the Pi package through Pi if it was installed there.

---

# Development status

RoastMyHarness is a work in progress.

Right now the focus is deliberately narrow: establish a trustworthy bare-Pi control, run modified Pi harnesses against the same software-engineering tasks, and collect enough behavioral and resource data to explain why the result moved.

There is plenty left to do. Real coding sessions are messier than DeepSWE. Other harnesses and other evaluation shapes are useful. Better longitudinal tests would help measure features whose payoff only appears after hours or multiple sessions.

Those are reasons to expand the benchmark, not reasons to skip measurement in the meantime.

RoastMyHarness is trying to move harness development in a simple direction:

**change something, hold the rest still, measure what happened, and use the result to decide what belongs in the agent.**

## License

MIT. See [LICENSE](LICENSE).
