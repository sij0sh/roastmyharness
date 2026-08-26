---
name: roast-my-harness
description: Drive the roast-my-harness CLI to design, validate, run, resume, and report Pi extension/skill comparison experiments on identical Pier tasks. Use when the user wants to compare harnesses, run an experiment, check run status, resume an interrupted run, or read results.
---

# RoastMyHarness

roast-my-harness compares Pi extensions and skills on identical Pier tasks.
One TOML file defines a control arm plus variants. The CLI runs every
variant x task cell and writes summary.csv, summary.json, and report.md.

## Prerequisites

- `roast-my-harness` on PATH (`uv tool install .` from the repo).
- Pier 0.3.x, docker, and node/npm in task containers.
- Codex auth: `roast-my-harness auth status`. If missing, the user must run
  `pi /login codex` once; the CLI reuses `~/.pi/agent/auth.json`.

## Workflow

1. Create a spec: `roast-my-harness init experiment.toml`
2. Edit the TOML. Set `name`, `pi_version`, `thinking`, `[model]`, and
   `[tasks] path`. Add one `[[variants]]` block per arm. Local extensions
   use `[[variants.extensions]]` with `kind = "local"`, `path`, `entry`.
   Skills use `[[variants.skills]]` with `kind = "local"` and a `path`
   containing SKILL.md. `[control]` toggles the bare-Pi arm.
3. Validate: `roast-my-harness validate experiment.toml`
   Fix every failure before running. Add `--json` when parsing output.
4. Run: `roast-my-harness run experiment.toml --yes`
   Always add `--yes` in non-interactive sessions. Ctrl-C cancels safely;
   completed cells persist on disk.
5. Watch: `roast-my-harness list`, then `roast-my-harness status <id>`.
6. Resume an interrupted run: `roast-my-harness resume <id>`
7. Report: `roast-my-harness report <id>`, then read `<run-dir>/report.md`
   and summarize pass/fail/error totals per variant for the user.

## User intent to command

| User says | Run |
| --------- | -- |
| "compare these extensions/skills" | init, edit, validate, run |
| "is my spec ok?" | validate |
| "how is the run going?" | list, then status |
| "it got interrupted" | resume |
| "what were the results?" | report, then read report.md |
| "check login" | auth status |

## Notes

- Experiment ids come from `list`; they encode the spec name and hash.
- `run` refuses to launch when preflight fails.
- `import-dse` imports legacy DSE results; they are reference-only and
  never reused as controls.
