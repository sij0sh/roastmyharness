---
name: roastmyharness
description: Drive the roast_harness tool to design, validate, run, resume, and report Pi extension/skill comparison experiments on identical Pier tasks. Use when the user wants to compare harnesses, run an experiment, check run status, resume an interrupted run, or read results.
---

# RoastMyHarness

roastmyharness compares Pi extensions and skills on identical Pier tasks.
One TOML file defines a control arm plus variants. The CLI runs every
variant x task cell and writes summary.csv, summary.json, and report.md.

## Prerequisites

- Pi or Claude Code with the roast_harness tool available
  (`roastmyharness setup --agent <pi|claude>` installs it).
- Pier 0.3.x, docker, and node/npm in task containers.
- Codex auth: `roastmyharness auth status`. If missing, the user must run
  `pi /login codex` once; the CLI reuses `~/.pi/agent/auth.json`.
- `roastmyharness doctor` verifies all of the above in one table.

## Tool usage

Prefer the `roast_harness` tool over shell commands in agent sessions; it
is the same code path with JSON responses.

1. Ask the user for the experiment TOML (or create one with
   `roastmyharness init` and edit it). Set `name`, `pi_version`,
   `thinking`, `[model]`, and `[tasks] path`. Add one `[[variants]]` block
   per arm.
2. Call roast_harness with `action: "prepare"` and `spec_path`. Review the
   returned plan, warnings, and `questions`. Fix every reported failure
   before proceeding. Stop and relay unconfirmed choices (`needs_input`)
   to the user.
3. Show the user the plan summary (tasks, arms, trials, max_parallel,
   model) and get explicit approval before starting.
4. Call roast_harness with `action: "start"` and the returned `plan_id`.
   Starting is idempotent per `plan_id`; a stale plan returns an error and
   you must re-prepare. By default the tool call stays open and streams
   live progress (per-cell results and state changes) until the
   experiment finishes; Esc only detaches the watch, the run continues.
   Pass `watch: false` to return immediately after launch, then attach
   later with `action: "watch"` and `experiment_id`.
5. If you did not watch (or detached), poll with `action: "status"` and
   `experiment_id` until the state is final.
6. Call `action: "report"`, then read `<run-dir>/report.md` and summarize
   pass/fail/error totals per variant for the user.
7. To stop a run, call `action: "cancel"`. To run only missing cells after
   an interruption, re-run prepare and start on the same spec (resume
   semantics are automatic).

## User intent to tool action

| User says | Do |
| --------- | -- |
| "compare these extensions/skills" | prepare, confirm, start (streams live) |
| "is my spec ok?" | prepare (it validates) |
| "how is the run going?" | watch (streams until final) or status |
| "stop the run" | cancel |
| "what were the results?" | report, read report.md |
| "check login" | `roastmyharness auth status` |
| "set it up" / "is it installed?" | `roastmyharness setup`, `doctor` |

## Notes

- Experiment ids come from status responses; they encode the spec name and
  hash.
- start refuses to launch when preflight fails.
- Tuning the live view: `interval_sec` (poll interval, default 2) and
  `recent` (trial events kept for display, default 20).
- Shell commands (`run`, `resume`, `list`, `status`, `watch`) remain
  available for interactive human use; `roastmyharness watch <id>` shows a
  live ASCII matrix in the terminal.
- `import-dse` imports legacy DSE results; they are reference-only and
  never reused as controls.
