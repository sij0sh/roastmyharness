# Rationale: structured-output example eval

## Target

A hypothetical skill claiming exact, machine-readable outputs
(JSON records, surgical file edits, formatted documents).

## Why these tasks

Each task isolates one capability from `capability-map.json` with a
fully deterministic verifier — no vision, no LLM judge. The eval
measures requirement adherence, the cheapest thing to get wrong and
the easiest to grade objectively.

The Test Designer saw only the capability map, never a skill source
(there is none: this target is illustrative). Nothing in the tasks
depends on skill-internal examples.

## Validation contract

`eval.toml` freezes the bar: `pass_threshold = 0.7`. Each verifier
computes a deterministic fraction over explicit checks and folds
`reward = 1.0` iff the fraction reaches the threshold, else `0.0`.
The harness grades the scalar and reports the fraction separately.

## Known limits

- Three tasks cannot cover real-world schema variety; this is a
  contract-format example, not a production benchmark.
- `edit-preserve` duplicates the seed text in `tests/test.sh` so the
  verifier can diff preservation. A production eval would ship the
  seed as a fixture artifact instead.
- No judge: visual polish and similar subjective axes are out of scope
  by design here.

## Freeze

Frozen 2026-09-01 as revision `2026-09-01`. Any edit to `eval.toml`
or the tasks changes the eval hash and starts a new run instead of
reusing prior cells.
