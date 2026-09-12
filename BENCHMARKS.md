# BENCHMARKS

This file is the running record of RoastMyHarness experiments.

The goal is not to turn every run into a leaderboard entry. It is to keep the
evidence behind harness decisions in one place: what changed, what stayed fixed,
what the result was, and how much confidence that result deserves.

1. **Summary** — the result in plain language.
2. **Record** — run identity and experimental conditions.
3. **Design** — what was held constant and what changed.
4. **Results** — the measured data.
5. **Paired outcomes** — rescued and broken tasks, not just aggregate score.
6. **Task-level evidence** — details that help explain important flips.
7. **Implications** — what the result supports, and what it does not.
8. **Caveats and provenance** — incidents, missing telemetry, and post-hoc recovery.
9. **Reproduce** — commands or analysis scripts when available.

### Reading conventions

A **rescue** means the treatment passed a task that the comparison arm failed.
A **break** means the treatment failed a task that the comparison arm passed.
At small sample sizes, those paired flips are more informative than a few
percentage points of aggregate resolve rate.

Smoke runs use one task to validate plumbing. They are not ranking evidence.

Cost is reported on the basis available from the harness. Claude Code values use
Claude Code's own list-price accounting. Pi runs through the gateways do not
currently expose per-call cost, so those cells remain blank rather than being
estimated.

---

## Token baselines: gpt-5.6-luna, low vs high thinking

### Summary

This is not a single A/B experiment. It pools stored RoastMyHarness runs through
2026-09-09 to establish a practical token baseline for `gpt-5.6-luna`.

The useful question is simple: **what does a normal run on this task usually
cost in tokens, and when is a new run far enough outside that range to deserve
inspection?**

Two patterns are already clear in the stored data. High thinking uses a much
larger and more consistent token budget than low thinking. Low-thinking runs
have a heavier tail, including several obvious runaway failures. These norms are
useful for triage, but many per-task estimates still have too few samples to be
treated as stable distributions.

Full per-task reasoning and total-token tables live in
`.agents/artifacts/token-analysis-gpt-5.6-luna.md`. The tables below keep the
headline output-token norms here.

### Record

| field | value |
|---|---|
| model | `gpt-5.6-luna` |
| thinking | `low`, `high` |
| window | 2026-08-27 to 2026-09-09 |
| run_dirs_scanned | 146 |
| experiments_contributing_trials | 124 |
| trials | 801 total, 768 usable |
| excluded_trials | 33 zero-token crashes |
| distinct_tasks | 77 |
| corpus | bundled DeepSWE datacurve corpus |
| baseline_trials | 138 low, 105 high, 56 tasks |
| analysis_script | `.agents/artifacts/token_stats_analysis.py` |

Stray medium/off trials exist in storage but are outside this analysis.

### Method

The norm uses baseline arms only: `control` or `baseline`, meaning unmodified Pi.
Treatment arms are pooled separately because changing the harness is expected to
change token behavior.

The metrics are:

| metric | meaning |
|---|---|
| `output_tokens` | generated tokens |
| `reasoning_tokens` | thinking tokens within output |
| `total` | input + cache-read + output; cumulative context consumption |
| `sigma` | sample standard deviation |
| `CV` | sigma / mean |

The 1-sigma and 2-sigma ranges are mean ± sigma and mean ± 2×sigma, clamped at
zero. Per-task sigma is unstable when `n < 5`; sparse tasks should use the pooled
fallback described below instead.

On the gateway used for these runs, `input_tokens` records only non-cached input,
while `cache_tokens` contains most of the prompt/context volume.
`peak_context_tokens` is near-constant for this model and is not useful here, so
it is excluded explicitly rather than treated as meaningful telemetry.

### Aggregate results

| thinking | n | output mean | output sigma | output median | output 2-sigma range | reasoning mean | total mean | total sigma |
|---|---|---|---|---|---|---|---|---|
| low | 565 | 4,790 | 9,626 | 3,211 | [0, 24.0k] | 400 | 246.4k | 176.0k |
| high | 203 | 32.2k | 28.2k | 25.5k | [0, 88.6k] | 13.7k | 4.86M | 4.44M |

The thinking modes separate strongly. Median reasoning is about 13.4k at high
thinking versus about 0.3k at low thinking, roughly a 40× difference. That makes
reasoning-token scale a cheap configuration check: a run that looks like the
wrong thinking mode should be inspected before its benchmark verdict is used.

### Per-task output norms: thinking = low

22 tasks, 138 baseline trials. Fifteen tasks have fewer than five samples, so
their task-specific sigma should be treated as provisional.

| task | n | mean | sigma | 1-sigma range | 2-sigma range | median | max | CV |
|---|---|---|---|---|---|---|---|---|
| helm-unified-manifest-stream | 4 | 19.8k | 32.2k | [0, 52.0k] | [0, 84.2k] | 4090 | 68.1k | 1.63 |
| bandit-incremental-cache-control | 18 | 10.3k | 20.6k | [0, 31.0k] | [0, 51.6k] | 3320 | 67.2k | 2.00 |
| obsidian-linter-link-format-conversion | 23 | 5910 | 13.3k | [0, 19.2k] | [0, 32.4k] | 3167 | 66.7k | 2.24 |
| httpx-deterministic-cookie-store | 3 | 4411 | 946 | [3466, 5357] | [2520, 6302] | 4041 | 5486 | 0.21 |
| vitest-duration-sharding | 3 | 4115 | 560 | [3555, 4675] | [2996, 5234] | 4293 | 4564 | 0.14 |
| go-critic-doc-link-checker | 4 | 3866 | 545 | [3321, 4411] | [2776, 4956] | 3886 | 4494 | 0.14 |
| vulture-persistent-analysis-cache | 3 | 3628 | 434 | [3194, 4062] | [2760, 4496] | 3713 | 4013 | 0.12 |
| koota-entity-snapshot-rollback | 2 | 3612 | 46 | [3567, 3658] | [3521, 3704] | 3612 | 3645 | 0.01 |
| fastapi-deprecation-response-headers | 3 | 3591 | 559 | [3032, 4150] | [2473, 4709] | 3601 | 4145 | 0.16 |
| go-git-worktree-merge-conflicts | 17 | 3476 | 591 | [2885, 4067] | [2295, 4658] | 3513 | 4319 | 0.17 |
| sqlfmt-create-table-ddl-formatting | 14 | 3312 | 881 | [2431, 4193] | [1549, 5074] | 3436 | 4596 | 0.27 |
| participle-grammar-conflict-analysis | 3 | 3302 | 193 | [3109, 3494] | [2916, 3687] | 3211 | 3523 | 0.06 |
| wazero-multi-module-snapshots | 17 | 3281 | 447 | [2834, 3728] | [2387, 4176] | 3216 | 4587 | 0.14 |
| bandit-structured-nosec-directives | 1 | 3253 | - | - | - | 3253 | 3253 | - |
| ts-pattern-match-each | 1 | 3240 | - | - | - | 3240 | 3240 | - |
| wasmi-trap-coredumps | 6 | 3085 | 222 | [2863, 3307] | [2641, 3529] | 3046 | 3420 | 0.07 |
| sql-formatter-bigquery-pipe-formatting | 5 | 2996 | 113 | [2884, 3109] | [2771, 3221] | 3051 | 3058 | 0.04 |
| numba-stencil-boundary-modes | 1 | 2874 | - | - | - | 2874 | 2874 | - |
| tengo-destructuring-bindings | 3 | 2823 | 481 | [2342, 3303] | [1861, 3784] | 3012 | 3180 | 0.17 |
| quill-shared-toolbar-focus | 2 | 2818 | 474 | [2344, 3293] | [1870, 3767] | 2818 | 3154 | 0.17 |
| obsidian-linter-auto-table-of-contents | 4 | 2762 | 238 | [2524, 3000] | [2286, 3238] | 2718 | 3075 | 0.09 |
| helm-array-merge-strategies | 1 | 2212 | - | - | - | 2212 | 2212 | - |

Task names are `datacurve/`-prefixed in the source data; the prefix is omitted
here for readability.

### Per-task output norms: thinking = high

49 tasks, 105 baseline trials. Forty-seven tasks have fewer than five samples.
For most of this table, the pooled fallback is more useful than the raw
task-specific sigma.

| task | n | mean | sigma | 1-sigma range | 2-sigma range | median | max | CV |
|---|---|---|---|---|---|---|---|---|
| optique-conditional-option-dependencies | 1 | 105.7k | - | - | - | 105.7k | 105.7k | - |
| dynamodb-toolbox-lazy-recursive-schemas | 1 | 69.6k | - | - | - | 69.6k | 69.6k | - |
| mashumaro-flattened-dataclass-fields | 2 | 64.1k | 46.7k | [17.5k, 110.8k] | [0, 157.5k] | 64.1k | 97.1k | 0.73 |
| fastapi-deprecation-response-headers | 2 | 52.3k | 47.9k | [4421, 100.2k] | [0, 148.1k] | 52.3k | 86.2k | 0.92 |
| obsidian-linter-link-format-conversion | 2 | 51.5k | 41.9k | [9644, 93.4k] | [0, 135.3k] | 51.5k | 81.1k | 0.81 |
| koota-composite-trait-aspects | 1 | 50.7k | - | - | - | 50.7k | 50.7k | - |
| pebble-durability-wait-apis | 2 | 37.5k | 492 | [37.0k, 38.0k] | [36.5k, 38.5k] | 37.5k | 37.9k | 0.01 |
| valibot-recursive-schema-composition | 1 | 37.2k | - | - | - | 37.2k | 37.2k | - |
| tomlkit-toml-table-converters | 2 | 36.2k | 795 | [35.4k, 37.0k] | [34.6k, 37.8k] | 36.2k | 36.7k | 0.02 |
| koota-deferred-mutation-buffer | 1 | 35.5k | - | - | - | 35.5k | 35.5k | - |
| quill-shared-toolbar-focus | 4 | 35.4k | 4054 | [31.3k, 39.5k] | [27.3k, 43.5k] | 33.6k | 41.5k | 0.11 |
| helm-unified-manifest-stream | 1 | 34.3k | - | - | - | 34.3k | 34.3k | - |
| tengo-destructuring-bindings | 3 | 29.6k | 1787 | [27.8k, 31.4k] | [26.1k, 33.2k] | 28.9k | 31.7k | 0.06 |
| csstree-shorthand-expansion-compression | 2 | 29.5k | 38 | [29.4k, 29.5k] | [29.4k, 29.6k] | 29.5k | 29.5k | 0.00 |
| etree-xml-diff-patch | 2 | 29.3k | 5809 | [23.5k, 35.1k] | [17.7k, 40.9k] | 29.3k | 33.4k | 0.20 |
| tengo-callable-instance-isolation | 4 | 28.7k | 4366 | [24.3k, 33.1k] | [20.0k, 37.4k] | 27.5k | 35.0k | 0.15 |
| python-statemachine-state-data-scoping | 1 | 28.6k | - | - | - | 28.6k | 28.6k | - |
| vulture-persistent-analysis-cache | 4 | 27.6k | 896 | [26.7k, 28.5k] | [25.8k, 29.4k] | 27.6k | 28.6k | 0.03 |
| cliffy-config-file-parsing | 2 | 27.3k | 2966 | [24.4k, 30.3k] | [21.4k, 33.3k] | 27.3k | 29.4k | 0.11 |
| oxvg-structural-selector-preservation | 1 | 27.1k | - | - | - | 27.1k | 27.1k | - |
| httpx-deterministic-cookie-store | 2 | 26.6k | 7362 | [19.3k, 34.0k] | [11.9k, 41.4k] | 26.6k | 31.9k | 0.28 |
| go-critic-doc-link-checker | 2 | 26.2k | 2265 | [23.9k, 28.4k] | [21.6k, 30.7k] | 26.2k | 27.8k | 0.09 |
| httpx-multipart-response-parsing | 2 | 25.8k | 936 | [24.8k, 26.7k] | [23.9k, 27.6k] | 25.8k | 26.4k | 0.04 |
| kysely-window-grouping-helpers | 2 | 25.6k | 1082 | [24.5k, 26.7k] | [23.4k, 27.8k] | 25.6k | 26.4k | 0.04 |
| arcane-drift-detection-baselines | 1 | 25.6k | - | - | - | 25.6k | 25.6k | - |
| ofetch-per-origin-circuit-breaker | 1 | 25.6k | - | - | - | 25.6k | 25.6k | - |
| aiomonitor-task-snapshots-diff | 1 | 25.5k | - | - | - | 25.5k | 25.5k | - |
| participle-grammar-conflict-analysis | 2 | 25.3k | 236 | [25.1k, 25.6k] | [24.9k, 25.8k] | 25.3k | 25.5k | 0.01 |
| vitest-duration-sharding | 2 | 24.9k | 135 | [24.7k, 25.0k] | [24.6k, 25.1k] | 24.9k | 25.0k | 0.01 |
| mobly-grouped-test-barriers | 3 | 24.9k | 1864 | [23.0k, 26.7k] | [21.1k, 28.6k] | 25.3k | 26.4k | 0.07 |
| returns-validated-error-accumulation | 4 | 24.3k | 1601 | [22.7k, 25.9k] | [21.1k, 27.5k] | 24.4k | 26.1k | 0.07 |
| bandit-incremental-cache-control | 4 | 24.3k | 1767 | [22.5k, 26.1k] | [20.8k, 27.8k] | 24.7k | 26.0k | 0.07 |
| cattrs-partial-structuring-recovery | 1 | 24.3k | - | - | - | 24.3k | 24.3k | - |
| obsidian-linter-auto-table-of-contents | 3 | 24.2k | 1208 | [23.0k, 25.4k] | [21.8k, 26.6k] | 24.8k | 25.0k | 0.05 |
| prometheus-typed-label-sorting | 6 | 24.0k | 2096 | [21.9k, 26.1k] | [19.8k, 28.2k] | 24.4k | 26.9k | 0.09 |
| prometheus-transactional-reload-status | 3 | 23.5k | 971 | [22.6k, 24.5k] | [21.6k, 25.5k] | 23.0k | 24.6k | 0.04 |
| drizzle-orm-window-function-builders | 1 | 23.3k | - | - | - | 23.3k | 23.3k | - |
| sqlfmt-create-table-ddl-formatting | 2 | 23.3k | 3823 | [19.4k, 27.1k] | [15.6k, 30.9k] | 23.3k | 26.0k | 0.16 |
| sql-formatter-bigquery-pipe-formatting | 2 | 22.5k | 235 | [22.2k, 22.7k] | [22.0k, 22.9k] | 22.5k | 22.6k | 0.01 |
| narwhals-rolling-window-suite | 1 | 22.1k | - | - | - | 22.1k | 22.1k | - |
| anko-typed-variable-bindings | 1 | 21.9k | - | - | - | 21.9k | 21.9k | - |
| onedump-dump-encryption-pipeline | 2 | 21.6k | 1855 | [19.8k, 23.5k] | [17.9k, 25.3k] | 21.6k | 22.9k | 0.09 |
| koota-entity-snapshot-rollback | 2 | 21.3k | 841 | [20.5k, 22.1k] | [19.6k, 23.0k] | 21.3k | 21.9k | 0.04 |
| actionlint-action-pinning-lint | 1 | 21.3k | - | - | - | 21.3k | 21.3k | - |
| sqlite-utils-safe-import-checkpoints | 1 | 21.1k | - | - | - | 21.1k | 21.1k | - |
| skrub-duration-encoding | 2 | 20.9k | 907 | [20.0k, 21.9k] | [19.1k, 22.8k] | 20.9k | 21.6k | 0.04 |
| kgateway-consistent-hash-policy | 4 | 20.7k | 1096 | [19.6k, 21.8k] | [18.5k, 22.9k] | 20.6k | 22.1k | 0.05 |
| wazero-multi-module-snapshots | 6 | 20.6k | 3527 | [17.1k, 24.1k] | [13.5k, 27.7k] | 20.2k | 26.4k | 0.17 |
| true-myth-iterable-collection-combinators | 2 | 19.5k | 938 | [18.6k, 20.5k] | [17.6k, 21.4k] | 19.5k | 20.2k | 0.05 |

### Flagged trials

These are baseline trials more than 2 sigma above or below their task norm.

| z | thinking | task | variant | date | output | resolved | reward |
|---|---|---|---|---|---|---|---|
| +4.6 | low | obsidian-linter-link-format-conversion | control | 2026-09-07 | 66.7k | 0 | 0.0 |
| +2.9 | low | wazero-multi-module-snapshots | control | 2026-09-07 | 4587 | 0 | 0.0 |
| +2.8 | low | bandit-incremental-cache-control | control | 2026-09-07 | 67.2k | 0 | 0.0 |
| +2.7 | low | bandit-incremental-cache-control | control | 2026-09-08 | 66.9k | 0 | 0.0 |

All four flagged trials in this dataset were unresolved failures. Three were
extreme 66-68k-output low-thinking runs on tasks whose normal output is much
smaller; the fourth was also an unresolved low-thinking outlier.

That is useful as a **triage signal**, not yet as a universal predictor. In this
sample, a large low-thinking token excursion and an unresolved result occurred
together. No high-thinking baseline trial breached its task-level 2-sigma norm.

### Sparse-task fallback

For a task with fewer than five samples, estimate its spread from the pooled
relative variation:

```text
sigma_hat = CV_pooled * task_mean
```

| thinking | CV median | CV p75 | CV p90 | guidance for 1-sigma band |
|---|---|---|---|---|
| low | 0.15 | 0.21 | 1.63 | mean +/- 0.15*mean |
| high | 0.07 | 0.09 | 0.11 | mean +/- 0.07*mean |

In this sample, high-thinking runs are relatively consistent around each task's
mean: median CV is about 0.07. Low thinking is noisier at median CV 0.15 and has a
much heavier runaway tail.

For single-run triage, the median CV is a better starting point than the low
p90. The low-thinking p90 of 1.63 is dominated by runaway behavior and would
make a poor definition of a normal band.

### Implications

The current baseline is good enough to answer two operational questions.

First, a reasoning-token count that is off by roughly an order of magnitude is a
strong reason to check whether the intended thinking level actually ran.

Second, a low-thinking run that lands far outside its task's normal output range
deserves inspection for looping or repeated work, especially if the task is
also unresolved.

The limit is sample depth. Most high-thinking tasks and many low-thinking tasks
still have fewer than five baseline observations. The tables should therefore be
treated as a living baseline, not frozen thresholds. As more control runs
accumulate, rerunning the analysis will make the per-task ranges more useful.

### Reproduce

```bash
python3 .agents/artifacts/token_stats_analysis.py
# rewrites .agents/artifacts/token-analysis-gpt-5.6-luna.md
# from ~/.local/share/roastmyharness/runs
```

---

## Snoop fixed retrieval (Phases A-E) vs historic arms

### Summary

The fixed Snoop retrieval path produced the clearest positive retrieval result
so far on `ipython-session-bundle-replay`: the old packet omitted the history
files the task needed, while both fixed runs surfaced them and passed the task.

`fastapi-implicit-head-options` is also consistent with an improvement in
retrieval quality, but only one replicate gives packet-level evidence because
the other passed without calling Snoop.

The important negative result is that the `boa` and `bandit` failures do not
look like retrieval regressions. Both sit on single-test capability edges, and
the packet comparisons do not show the fixed retrieval dropping the evidence
needed to solve them.

The headline totals vary enough between replicates that they should not be used
to rank the arms. The useful signal is in the stable flips and packet-level
differences.

### Record

| field | value |
|---|---|
| run_id | `snoop-fixed-fac02adf` (rep 1), `snoop-fixed-d231ee72` (rep 2) |
| date | 2026-09-09 |
| status | rep 1 COMPLETE; rep 2 matrix complete |
| historic_comparison | `snoop-smoke-b120e547` |
| historic_status | CANCELLED after 31 trials |
| spec | `snoop-fixed.toml` |
| treatment | Snoop Phases A-E |
| model | `gpt-5.6-luna` |
| thinking | `high` |
| paired_tasks | 14 |
| excluded_probe_artifacts | 4 in rep 2 |
| control_in_new_runs | disabled |
| snoop_build | Debian 12 binary rebuilt after retrieval changes |

The treatment includes the per-commit cap, locale collapse, lane gating, and
history-fill gating. The new runs contain the Snoop arm only, so the comparison
uses resolved control and old-Snoop trials from the historic run.

### Design

The analysis is restricted to the 14 tasks for which both historic arms had a
resolved result. The two fixed Snoop runs are treated as replicates of the same
retrieval change.

This is not a clean fresh-control A/B run. Its value comes from paired historical
task outcomes plus direct packet inspection: queries, item kinds, commit SHAs,
code locators, and verifier results.

### Results

Legend: `P` = pass, `F` = fail.

| Task | control | old-snoop | fixed r1 | fixed r2 |
|---|---|---|---|---|
| arcane-drift-detection-baselines | P | P | P | P |
| bandit-incremental-cache-control | P | F | P | F |
| boa-hierarchical-evaluation-cancellation | F | P | F | F |
| cattrs-partial-structuring-recovery | P | P | F | P |
| clack-async-autocomplete-options | F | F | F | F |
| claude-code-by-agents-recursive-delegation | P | F | P | P |
| etree-xml-diff-patch | F | F | P | F |
| fastapi-implicit-head-options | P | F | P | P |
| httpx-deterministic-cookie-store | P | P | P | P |
| ipython-session-bundle-replay | F | F | P | P |
| kcp-go-multiplexed-kcp-streams | P | F | P | F |
| kgateway-consistent-hash-policy | F | F | F | F |
| kombu-single-active-consumer-priority | P | P | P | F |
| kombu-virtual-queue-dead-lettering | F | F | F | F |
| **Totals** | **8** | **5** | **9** | **6** |

The fixed replicates resolve 9/14 and 6/14 tasks respectively, versus 5/14 for
historic old-Snoop and 8/14 for the historic control. The spread between the two
fixed runs is large enough that totals alone are not a reliable conclusion.

### Paired outcomes

Fixed Snoop compared with historic old-Snoop:

| outcome | tasks |
|---|---|
| rescued in both replicates | `fastapi-implicit-head-options`, `claude-code-by-agents-recursive-delegation`, `ipython-session-bundle-replay` |
| broken in both replicates | `boa-hierarchical-evaluation-cancellation` |
| split across replicates | `bandit-incremental-cache-control`, `kcp-go-multiplexed-kcp-streams`, `cattrs-partial-structuring-recovery`, `etree-xml-diff-patch`, `kombu-single-active-consumer-priority` |
| stable pass | `arcane-drift-detection-baselines`, `httpx-deterministic-cookie-store` |
| stable fail | `clack-async-autocomplete-options`, `kgateway-consistent-hash-policy`, `kombu-virtual-queue-dead-lettering` |

The three repeat rescues are the strongest outcome signal. The split tasks should
be treated as run variance until more repetitions say otherwise.

### Task-level evidence

| task | observed packet/verifier difference | interpretation |
|---|---|---|
| `ipython-session-bundle-replay` | Old packet omitted `history.py`, `historyapp.py`, `magics/history.py`, and `test_history.py`. Both fixed packets contain all four. Old packet also repeated commit `aaa5a456` three times; fixed packets cap it at two. | Strongest retrieval-attributable win. Both fixed runs pass, while old Snoop and control fail. |
| `fastapi-implicit-head-options` | Historic q1 packet contained eight translated `first-steps.md` siblings plus a triplicated commit. Fixed rep 2 keeps English docs and includes `routing.py`, `models.py`, and security files. | Retrieval-plausible in rep 2. Rep 1 passed without any `context` call, so that replicate cannot be credited to Snoop. |
| `claude-code-by-agents-recursive-delegation` | The old packet repeated commit `6a9c59a5` three times; fixed packets do not. Agent queries differ across all runs. | Weak positive. Outcome improved, but retrieval is not isolated as the cause. |
| `boa-hierarchical-evaluation-cancellation` | No commit is uniformly missing from the fixed packets. Both fixed runs produce ~29 KB patches and fail the same 16/17 test, `cancelled_session_jobs_are_skipped_but_unrelated_jobs_still_run`. | No packet-level evidence that the retrieval fix caused the loss. More consistent with a capability-edge flip. |
| `bandit-incremental-cache-control` | All three Snoop runs reach 88-89/89. Historic old-Snoop and fixed rep 2 fail the same file-size edge test; rep 1 passes. | Single-test variance, not a clear retrieval effect. |

A separate A5 replay found no code/doc locator that was uniformly lost after the
retrieval changes. That matters because the positive packet changes are not
paired with an obvious broad recall regression in this sample.

### Implications

The fixed retrieval work has at least one convincing success case: `ipython`.
There, the old packet was missing the files required by the task and both fixed
packets supplied them.

`fastapi` is a useful second example of the intended mechanism: less translation
noise, no triplicated commit, and more relevant implementation files. Because
one replicate succeeded without using Snoop, it should be described as
supporting evidence rather than a clean causal win.

The data does **not** support saying that fixed Snoop is globally better than
either historic arm. Replicate totals are too unstable, and several tasks split
between runs. The result supports a narrower conclusion: the retrieval changes
fixed identifiable packet defects without producing an identifiable packet-level
regression on the two most concerning losses.

### Caveats and provenance

The first 30-task `snoop-fixed` launch was terminated at the wrapper rather than
the runner. The runner survived and later enumerated the narrowed 14-task spec,
producing `snoop-fixed-fac02adf` as rep 1.

Rep 2, `snoop-fixed-d231ee72`, ran the same 14 tasks plus four probe artifacts.
The arktype/optique probe errors and mobly/onedump probe failures are excluded
from the tables above.

Both fixed runs also overlapped with each other and with unrelated SWE
experiments. CPU contention may have increased wall-time and outcome variance.

A stale runner from the `fac02adf` launch remained after completion and was
reaped. No trial result above was affected.

### Reproduce

This historical comparison does not record a single standalone reproduction
command in this file. The auditable inputs are the two fixed run IDs, the
historic run ID, and `snoop-fixed.toml`.

---

## Claude Code bare vs Pi bare

### Summary

This smoke test held the model and thinking level constant while changing the
coding harness: Pi versus Claude Code.

Claude Code passed the one DeepSWE task and Pi missed one of 24 verifier tests.
Claude also used substantially more input/output tokens and took six minutes
longer.

With one task, that is **not** evidence that Claude Code is the better harness.
It is evidence that the plumbing worked and that this particular task produced
a very narrow semantic difference worth following on a larger paired run.

### Record

| field | value |
|---|---|
| run_id | `pi-vs-claude-bare-ed2d5b93` |
| date | 2026-09-09 |
| status | COMPLETE |
| spec | `pi-vs-claude-bare.toml` |
| branch | `ad-hoc/claude-bare` |
| harness | roastmyharness 0.1.0, pier 0.3.1 |
| model | `anthropic-gateway/claude-opus-5` |
| thinking | `low` |
| pi_version | 0.85.1 |
| claude_code_version | 2.1.266 |
| corpus | bundled DeepSWE |
| preset | `luna-signal` |
| catalog_revision | 2026-09 |
| task_hash | `90592907baf82f7bcd84c4eccce0b1229c63711f0e6e8ecf19c362ce01b6f7a0` |
| task_count | 1 |
| repetitions | 1 per arm |
| concurrency | 1 |
| run_dir | `~/.local/share/roastmyharness/runs/pi-vs-claude-bare-ed2d5b93` |

Claude-arm support on this branch was restored from commit `40f6383`.

### Design

The task was `boa-hierarchical-evaluation-cancellation`, a medium-band DeepSWE
task in the Boa JavaScript engine. The verifier contains 24 tests: 17
fail-to-pass and 7 pass-to-pass. The task receives reward 1.0 only when all 24
pass.

The model, thinking level, task instruction, container image, egress allowlist,
and git identity were the same on both arms. The harness contracts were:

| arm | contract |
|---|---|
| Pi bare | `-nc --no-skills --no-prompt-templates --no-themes`, no extensions, no skills |
| Claude Code bare | `--strict-mcp-config`, pinned `settings.json` with `bypassPermissions`, telemetry/auto-update off, no MCP servers |

Holding the model constant removes model choice as a confound. It does not remove
normal run-to-run variance, which matters especially at `n=1`.

### Results

| metric | pi bare | Claude Code bare |
|---|---|---|
| Outcome | FAIL (reward 0.0) | PASS (reward 1.0) |
| Verifier | 23/24 tests | 24/24 tests |
| Wall time | 21 min | 27 min |
| Input tokens (cached) | 2,651k | 5,145k |
| Input tokens (uncached) | ~0 | 5,283k total / 138.6k cache-write |
| Output tokens | 32k | 49,866 (incl. 15,775 thinking) |
| Cost | not surfaced by gateway | $4.78 (list-price basis) |
| Tool calls | 50 | 61 |
| Turns / recorded steps | - | 62 turns, 56 ATIF steps |
| Peak context tokens | 81k | not captured (see provenance) |

### Paired outcomes

Claude Code compared with Pi:

| outcome | count | tasks |
|---|---:|---|
| rescued | 1 | `boa-hierarchical-evaluation-cancellation` |
| broken | 0 | — |
| both pass | 0 | — |
| both fail | 0 | — |

This corrects the earlier label in this file: Claude passing a task that Pi
failed is a **rescue**, not a break.

### Task-level evidence

Pi reached 23/24 verifier tests. All seven pass-to-pass tests remained green, and
16 of 17 fail-to-pass tests turned green. The remaining failure was:

```text
[f2p] boa_engine: tests::evaluation::
  cancelled_session_jobs_are_skipped_but_unrelated_jobs_still_run
```

The gap is narrow. Pi implemented cancellation, reason propagation, and
rejection of enqueueing onto cancelled handles, but its job-skipping behavior
did not preserve the invariant that unrelated jobs continue running after a
session is cancelled.

DeepSWE scores the task all-or-nothing, so that one semantic edge case produces
reward 0.0. Claude Code passed all 24 tests.

### Implications

The useful result is not “Claude won 1-0.” The sample is far too small for that.

The useful result is that two harnesses running the same model reached nearly
the same implementation, and the final difference was one semantic edge case.
That makes this task a good example of the near-miss region where harness
behavior, tool ergonomics, prompt wiring, or simple run variance may decide the
verdict.

The resource shape is also worth tracking on a larger run. Claude used about
twice Pi's cached input, generated more output, made more tool calls, and took
six minutes longer. At `n=1` those numbers are descriptive, not a stable cost
ratio.

### Caveats and provenance

Claude's token and cost cells initially appeared as zero. Claude Code wrote its
transcript and `.claude.json` with restrictive permissions, and Pier preserved
those permissions during host-side relocation. The ATIF converter then failed
with `PermissionError` and silently dropped the metrics.

The scan was fixed in `f628ec7`, and container-side permissions were fixed in
`c8a134a`. The values above were recovered from the original trial artifacts by
converting the transcript with Pier's ATIF converter and folding the metrics
back into `result.json`. The task was not rerun, and the pass/fail verdicts did
not change.

Claude cost is Claude Code's list-price accounting. Actual gateway cost may
differ.

Long Rust builds also produced heartbeat keep-alives in the Claude transcript,
so wall-time differences on build-heavy tasks include some harness/runtime
noise.

### Reproduce

```bash
roastmyharness validate pi-vs-claude-bare.toml
roastmyharness run pi-vs-claude-bare.toml
roastmyharness watch <run-id>
```

Requires `GATEWAY_API_KEY`, Docker, and the `ad-hoc/claude-bare` branch. Claude
arm support is not on main.

---

## Pi bare vs OMP bare (partial, cancelled)

### Summary

This was the first cross-agent comparison in the log and the run that drove OMP
support.

On the four tasks completed by both arms, Pi resolved 2/4 and OMP resolved 1/4.
The stronger finding is not the score difference. OMP used about 3.3× as much
cached input, about 1.9× as many agent steps, and about 1.7× the wall time, while
not producing an extra pass in the matched sample.

The run was cancelled before OMP reached five of the nine tasks, so outcome
ranking would overstate what the data can support.

### Record

| field | value |
|---|---|
| run_id | `omp-9task-53a89fc3` |
| precursor_smoke_run | `omp-live-smoke-16a9f2f7` |
| date | 2026-08-28 |
| status | CANCELLED |
| harness | roastmyharness 0.1.0, pier 0.3.x, schema v1 |
| model | `gpt-5.6-luna` |
| thinking | `high` |
| pi_version | 0.84.3 |
| omp_version | oh-my-pi 18.0.9 |
| corpus | DeepSWE via external DSE-tests checkout |
| task_count | 9 hand-picked |
| completed_by_pi | 7/9 |
| completed_by_omp | 4/9 |
| matched_tasks | 4 |
| run_dir | `~/.roastmyharness/runs/omp-9task-53a89fc3` |

This predates the bundled corpus, catalog, and current experiment schema.

### Design

The nine tasks covered TypeScript, Go, Python, rendering, and parser work.
`per_variant = 2`; the control was enabled; there were no repetitions.

The model, thinking level, instruction, container image, egress allowlist, and
git identity were shared across the arms. The bare-harness contracts were:

| arm | contract |
|---|---|
| Pi bare | `-nc --no-skills --no-prompt-templates --no-themes`, no extensions, no skills |
| OMP bare | `--no-skills` plus staged `config.yml` disabling implicit provider-config auto-load; Bun pinned because task images shadow it |

As with the Claude smoke, holding the model constant removes model choice as a
confound but does not eliminate run variance.

### Results

| task | pi | omp | pi f2p | omp f2p | verdict |
|---|---|---|---|---|---|
| awilix-async-container-initialization | F | F | 23/24 | 22/24 | omp misses one more test than pi |
| clack-async-autocomplete-options | F | F | 79/82 | 72/82 | omp misses 10 vs pi's 3 |
| etree-xml-diff-patch | P | F | 52/52 | 51/52 | broken flip: omp one test short |
| happy-dom-abort-pending-body-reads | P | P | 14/14 | 14/14 | both pass |

Control-only (omp cancelled before starting): httpx (pi F, 114/115),
ipython (pi P), katex (pi F, 92/94). Never started on either arm:
optique, numba.

| metric (4 matched tasks) | pi control | omp |
|---|---|---|
| Resolved | 2/4 | 1/4 |
| Cached input tokens | 11.9M | 39.5M (3.3x) |
| Output tokens | 114.4k | 141.2k (1.2x) |
| Agent steps | 222 | 421 (1.9x) |
| Wall time (sum) | 58m | 101m (1.7x) |
| Errors / timeouts / empty patches | 0 | 0 |

OMP's cached-input amplification is present on every matched task. The per-task
OMP/Pi ratios are 2.75×, 6.58×, 3.79×, and 2.85×, so the aggregate 3.3× ratio is
not being driven by one isolated outlier.

### Paired outcomes

OMP compared with Pi on the four matched tasks:

| outcome | count | tasks |
|---|---:|---|
| rescued | 0 | — |
| broken | 1 | `etree-xml-diff-patch` |
| both pass | 1 | `happy-dom-abort-pending-body-reads` |
| both fail | 2 | `awilix-async-container-initialization`, `clack-async-autocomplete-options` |

Three tasks were completed only by Pi before cancellation: `httpx` (FAIL,
114/115), `ipython` (PASS), and `katex` (FAIL, 92/94). `optique` and `numba`
never started on either arm.

### Task-level evidence

#### `etree-xml-diff-patch`

OMP reached 51/52 tests and failed one fail-to-pass case:

```text
[f2p] github.com/beevik/etree.TestMerge3WayStructuralConflict
```

Pi passed all 52. This is another all-or-nothing near miss: a single semantic
edge case in three-way merge conflict resolution changed the task verdict.

#### Both-fail tasks

On `awilix`, Pi missed one test:
`allows scope.initialize() without calling parent.initialize`. OMP missed that
same test plus
`initialization failure triggers rollback leaves container in failed state`.

On `clack`, Pi missed 3 tests and OMP missed 10. The failures cluster around
AbortController, retry, and loading-state semantics. OMP took 102 steps versus
Pi's 32 but still left more of that cluster unresolved.

The extra activity therefore did not translate into better coverage on these
matched failures.

### Precursor smoke

The same-day smoke run, `omp-live-smoke-16a9f2f7`, used
`ofetch-per-origin-circuit-breaker`.

Both arms passed all 60 tests. Pi took 9.5 minutes, used 1.9M cached-input
tokens, and recorded 42 steps. OMP took 19.7 minutes, used 5.2M cached-input
tokens (2.7×), and recorded 63 steps.

That smoke did what a smoke test should do: it validated the OMP plumbing. It
also foreshadowed the higher context and step count later seen across the four
matched tasks.

### Implications

There is not enough completed task coverage to rank Pi and OMP.

There **is** a repeatable cost-shape signal in this sample. Across the matched
tasks and the precursor smoke, OMP reprocessed substantially more context, took
more steps, and ran longer. Those extra turns did not create an additional pass
in the four-task matched set.

The semantic misses also cluster in similar async lifecycle areas across both
arms. That suggests the shared model is responsible for at least some of the
common blind spots, while the harness changes how much work the model does
around them.

The recurring one-test near misses on strict all-or-nothing verifiers are worth
tracking in future harness comparisons. At this scale, a harness difference may
show up as one edge case rather than a broad capability gap.

### Caveats and provenance

The run was cancelled before finalization, so no summary/report was generated at
run time.

The numbers here were reconciled on 2026-09-09 from the original trial
artifacts: `result.json`, `verifier/ctrf.json`, and `verifier/reward.json`, using
`.agents/artifacts/omp_deep_dive.py`. Wall times come from
`result.json` start/finish timestamps. Nothing was rerun, and the recorded
pass/fail verdicts were not changed.

This harness version did not capture the reasoning-token split or tool-call
counts in the adapter payload. `peak_context_tokens` is also unusable for the
same telemetry reason described in the token-baseline section.

Because this run predates the current bundled corpus and schema, its identity
hashes should not be compared directly with current experiments.

### Reproduce

```bash
python3 .agents/artifacts/omp_deep_dive.py   # per-trial metrics + flips
roastmyharness status omp-9task-53a89fc3     # matrix (from the merged DB)
```

The run data lives under `~/.roastmyharness/runs/`. The database row was migrated
with `run_dir` rewritten, so status/report commands resolve against the new
location.

---

## Bash only vs full-tool Pi

### Summary

Restricting Pi to the bash tool does not degrade resolve rate on the tested
pools. On `gpt-5.6-luna` (30 tasks x 2 reps) bash-only resolved 32/60 against
28/60 for the fresh full-tool control, with 7 rescued tasks against 3 broken.
On `muse-spark-1.3-contributor` (6 tasks x 1 rep) bash-only resolved 5/6 and
matched the historic control on every task.

The stronger signal is cost shape. Bash-only used 44% less cached-prefix
traffic, 41% fewer tool calls, and 18% less wall time than control on the luna
run while resolving more trials. The restriction changes how much work the
model does, not whether it can do the work.

The first bash-only attempt failed before this result existed: `pi_flags`
carried `--tools=bash`, which Pi 0.85.1 rejects (`Unknown option: --tools`).
All three trials exited in under a second with zero tokens. That failure is
harness plumbing, not model evidence, and it is fixed (see Caveats).

### Record

| field | value |
|---|---|
| run_id | `roast-20260912t013353-17ea331a` |
| companion_run | `roast-20260912t001343-339374cc` (muse-spark, 6 tasks) |
| precursor_run | `roast-20260911t233258-e19545f0` (flag-form failure) |
| date | 2026-09-12 |
| status | COMPLETE (both runs) |
| spec | `roast-20260912t013353.toml`, `roast-20260912t001343.toml` |
| harness | roastmyharness 0.1.0, pier 0.3.x, schema v3 |
| model | `gpt-5.6-luna` (main), `muse-spark-1.3-contributor` (companion) |
| thinking | `high` |
| pi_version | 0.85.1 |
| corpus | bundled DeepSWE datacurve corpus |
| task_count | 30 (main), 6 (companion) |
| repetitions | 2 (main), 1 (companion) |
| run_dir | `~/.roastmyharness/runs/roast-20260912t013353-17ea331a` |

### Design

The treatment arm is base Pi with `pi_flags = ["--no-builtin-tools", "--tools",
`bash"]`, i.e. no built-in read/edit/write/grep/find/ls tools and no
restriction beyond bash. The control arm is fresh full-tool Pi launched
side by side on the main run; the companion run sets `control = false` and
compares against historic control arms on the same model and thinking level.

The model, thinking level, instruction, container image, and session handling
were shared across arms. The bare-harness fairness flags (`--no-skills
--no-prompt-templates --no-themes`) apply to both arms.

### Results

Main run (luna, 30 tasks x 2 reps):

| metric (60 trials per arm) | bash-only | control |
|---|---|---|
| Resolved | 32/60 (53.3%) | 28/60 (46.7%) |
| Output tokens | 1.61M | 1.77M |
| Cached-prefix tokens | 145.6M | 260.7M |
| Tool calls | 2,845 | 4,858 |
| Wall time (sum) | 9.8h | 11.9h |

Companion run (muse-spark, 6 tasks x 1 rep, no fresh control):

| task | bash-only | historic control |
|---|---|---|
| fastapi-implicit-head-options | P (43/43) | 1/2 (fail in e19545f0, pass in 07c0e565) |
| ipython-session-bundle-replay | P (17/17) | P |
| kgateway-consistent-hash-policy | P (2/2) | P |
| kombu-single-active-consumer-priority | P (85/85) | P |
| obsidian-linter-auto-table-of-contents | F (0/41) | F |
| returns-validated-error-accumulation | P (159/159) | P |

Companion totals: 912k input / 261k output tokens, mean 55 tool calls and
zero read calls per trial, 8m mean wall. Bash-only burned fewer input tokens
than control on fastapi, ipython (77k vs 263k), and kombu, and more on
kgateway (+159k) and returns (+29k).

### Paired outcomes

Main run, bash-only compared with control (per-task reps):

| outcome | count | tasks |
|---|---:|---|
| rescued | 7 | `claude-code-by-agents-recursive-delegation`, `etree-xml-diff-patch`, `kombu-virtual-queue-dead-lettering`, `mobly-grouped-test-barriers`, `pebble-durability-wait-apis`, `valibot-recursive-schema-composition`, `wazero-multi-module-snapshots` |
| broken | 3 | `bandit-incremental-cache-control`, `kgateway-consistent-hash-policy`, `numba-stencil-boundary-modes` |
| both pass | 5 | `arcane-drift-detection-baselines`, `kcp-go-multiplexed-kcp-streams`, `prometheus-typed-label-sorting`, `tengo-callable-instance-isolation`, `vulture-persistent-analysis-cache` |
| split | 8 | `boa-hierarchical-evaluation-cancellation`, `cattrs-partial-structuring-recovery`, `httpx-deterministic-cookie-store`, `ipython-session-bundle-replay`, `kombu-single-active-consumer-priority`, `optique-conditional-option-dependencies`, `returns-validated-error-accumulation`, `ts-pattern-match-each` (1/2 on both arms) |
| both fail | 7 | `arktype-json-schema-refs-dependencies`, `clack-async-autocomplete-options`, `fastapi-implicit-head-options`, `onedump-dump-encryption-pipeline`, `prometheus-transactional-reload-status`, `python-statemachine-state-data-scoping`, `skrub-duration-encoding` |

Companion run: 1 rescued relative to the latest control
(`fastapi-implicit-head-options`), 0 broken, 4 concordant passes, 1
concordant fail (`obsidian-linter-auto-table-of-contents`).

### Task-level evidence

`obsidian-linter-auto-table-of-contents` failed on every arm in both runs
(historic control, ponytail, bash-only; 0/41 f2p on the companion). It reads
as the hardest task in the pool rather than a bash-only weakness.

`fastapi-implicit-head-options` is the informative flip. The luna main run
failed it on both arms (0/2 each), while muse-spark bash-only passed it 43/43
after the luna-control configurations split 1/2 historically. The task is
model-sensitive; the tool restriction is not what decides it.

The three luna breaks (`bandit`, `kgateway`, `numba`) share no obvious task
class, and `kgateway` was also the companion run's most expensive pass (753s,
99 tool calls). No pattern in the breaks points at a specific missing tool.

### Implications

Removing the read/edit/write/search builtins does not remove the model's
ability to solve these tasks: both models match or beat their controls with
bash alone, including zero read-tool calls on the companion run. The
repeatable effect is efficiency, not capability: fewer tool calls, less
reprocessed context, less wall time.

The 7-vs-3 flip imbalance on the main run favors bash-only but sits inside
overlapping intervals at 30 tasks, so it should be read as "no degradation"
rather than "bash-only is better." A larger task pool would be needed to
promote the direction to a claim.

### Caveats and provenance

The precursor run `roast-20260911t233258-e19545f0` recorded 0/3 for bash-only
with `NonZeroAgentExitCodeError` on every trial: the spec passed
`--tools=bash` (equals form) and Pi 0.85.1 only accepts the space form
(`--tools bash`). Those zeros are launch failures and must not be pooled with
the results above.

Fixed in commit `0044800a87d9`: `normalize_extra_flags()` in
`src/roast_my_harness/adapter/command.py` expands `--flag=value` to two
tokens, and the `pi_flags` validator in `src/roast_my_harness/spec/models.py`
accepts both forms while rejecting dangling flags, stray values, and `=` on
boolean flags. Both runs here already use the corrected space form.

The companion run has no fresh control arm (`control = false`); its baseline
is historic control arms on the same model and thinking level, so run-variance
caveats apply more strongly there than on the main run.

Cost cells are $0.00 on all arms because the gateway reports no per-call cost;
the token columns carry the comparison.

### Reproduce

```bash
roastmyharness status roast-20260912t013353-17ea331a
roastmyharness status roast-20260912t001343-339374cc
```

Per-task flips and token tables were derived from each run's `summary.csv`
with the standard csv columns (`variant,task,resolved,input_tokens,
output_tokens,cache_tokens,tool_calls,wall_sec`). A narrative final report for
the main run lives at
`~/.roastmyharness/runs/roast-20260912t013353-17ea331a/final-report.md`.

---

## Adding a new experiment

Add new experiments above the older ones and keep the same section order when it
fits the run.

### Summary

Write two or three short paragraphs answering:

- What changed?
- What is the strongest result?
- What should **not** be concluded from this sample?

### Record

Use a two-column `field | value` table. Prefer these field names when applicable:

| field | expected value |
|---|---|
| `run_id` | immutable run identifier |
| `date` | run date |
| `status` | COMPLETE, CANCELLED, partial, etc. |
| `spec` | experiment spec filename |
| `branch` | branch or commit context when relevant |
| `harness` | RoastMyHarness / Pier versions |
| `model` | provider/model or model id |
| `thinking` | thinking level |
| `pi_version` | Pi version when relevant |
| `corpus` | benchmark corpus |
| `preset` | named task preset |
| `task_count` | number of intended tasks |
| `matched_tasks` | tasks with comparable results across arms |
| `repetitions` | repetitions per task/arm |
| `run_dir` | stored run path when useful |

Extra experiment-specific fields are fine, but reuse the same field name for the
same concept across sections.

### Design

State what changed between arms and what was held constant. If “bare” has a
specific contract, record it in a table.

### Results

Keep measured values in Markdown tables with stable column names. Do not mix
interpretation into metric cells. Put units in either the column name or the
value consistently.

### Paired outcomes

Use this schema whenever there is a control/comparison arm:

| outcome | count | tasks |
|---|---:|---|
| rescued |  |  |
| broken |  |  |
| both pass |  |  |
| both fail |  |  |

If replicates disagree, add a `split` row instead of forcing a verdict.

### Task-level evidence

Use this only for flips or failures that materially explain the experiment.
Prefer a table when several tasks are discussed.

### Implications

Separate observed facts from interpretation. State the narrowest conclusion the
data supports first, then the limits imposed by sample size, variance, or
benchmark design.

### Caveats and provenance

Record cancellations, concurrent runs, telemetry gaps, post-hoc recovery, or
anything else that could change how the numbers should be read. Keep these
disclosures inline with the experiment rather than in a separate hidden log.

### Reproduce

Record the exact command or analysis script when one exists. If an old run
cannot be reproduced cleanly under the current schema, say so instead of
inventing an equivalent command.
