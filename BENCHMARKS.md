# BENCHMARKS.md

Log of A/B experiments run with RoastMyHarness. One section per
experiment, newest first. Each section records identity, design,
results, and interpretation so numbers stay auditable after the fact.

How to read:

- Resolve rate alone is noise at small n. Paired flips are the signal:
  which tasks flipped from fail to pass (rescued) or pass to fail
  (broken) between arms.
- Smoke runs (1 task) exist to validate plumbing, not to rank agents.
- Cost is list-price basis for claude arms (Claude Code's own
  accounting) and blank for pi arms on the gateways, which do
  not surface per-call costs.

---

## Token baselines: gpt-5.6-luna, low vs high thinking

Not one experiment but a cross-run aggregate: every stored RoastMyHarness
run through 2026-09-09 that used gpt-5.6-luna, pooled to answer one
question - what is the normal token footprint per task, and when is a
new run far outside the norm? Full per-task tables for reasoning and
total tokens live in
`.agents/artifacts/token-analysis-gpt-5.6-luna.md`; this section carries
the headline output-token norms inline.

### Identity and scope

| field | value |
|---|---|
| Model | `gpt-5.6-luna` |
| Thinking levels | `low`, `high` (stray medium/off trials exist, out of scope) |
| Window | 2026-08-27 to 2026-09-09 |
| Run dirs scanned | 146 |
| Experiments contributing trials | 124 |
| Trials | 801 total, 768 usable (33 zero-token crashes excluded) |
| Distinct tasks seen | 77 (bundled DeepSWE datacurve corpus) |
| Baseline-arm trials (the norm) | 138 low, 105 high, 56 tasks |
| Analysis script | `.agents/artifacts/token_stats_analysis.py` |

### Method

- "Norm" tables pool baseline arms only (`control`/`baseline`: the
  unmodified pi harness). Treatment arms change token behaviour by
  design, so they are excluded from the norm and pooled separately.
- Metrics: `output_tokens` = generated tokens; `reasoning_tokens` =
  thinking subset of output; `total` = input + cache-read + output
  (cumulative context consumption).
- sigma is the sample standard deviation; 1-sigma and 2-sigma ranges
  are mean +/- sigma and mean +/- 2*sigma, clamped at 0. CV = sigma/mean.
- Per-task sigma from n < 5 samples is unstable. For those tasks use
  the pooled fallback below: sigma_hat = CV_pooled * task_mean.
- Accounting on the gateway used: `input_tokens` counts only non-cached
  input (tens to hundreds per trial); `cache_tokens` carries the bulk.
  `peak_context_tokens` is not usable for this model (near-constant
  value) and is excluded - a telemetry gap, disclosed here rather than
  silently dropped.

### Model-level overview

| thinking | n | output mean | output sigma | output median | output 2-sigma range | reasoning mean | total mean | total sigma |
|---|---|---|---|---|---|---|---|---|
| low | 565 | 4,790 | 9,626 | 3,211 | [0, 24.0k] | 400 | 246.4k | 176.0k |
| high | 203 | 32.2k | 28.2k | 25.5k | [0, 88.6k] | 13.7k | 4.86M | 4.44M |

Thinking levels separate cleanly: high median reasoning is ~40x low
(13.4k vs 0.3k). A low run producing high-level reasoning counts, or
vice versa, is a config error, not noise - the cheapest anomaly check
in the stack.

### Per-task output norms: thinking = low (22 tasks, 138 trials)

15 of 22 tasks have n < 5; treat their sigma as provisional.

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

Task names are `datacurve/`-prefixed; the prefix is dropped for width.

### Per-task output norms: thinking = high (49 tasks, 105 trials)

47 of 49 tasks have n < 5 - the pooled fallback is the primary guide
here, the per-task sigma is directional only.

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

### Flagged trials (beyond 2 sigma of their task norm)

| z | thinking | task | variant | date | output | resolved | reward |
|---|---|---|---|---|---|---|---|
| +4.6 | low | obsidian-linter-link-format-conversion | control | 2026-09-07 | 66.7k | 0 | 0.0 |
| +2.9 | low | wazero-multi-module-snapshots | control | 2026-09-07 | 4587 | 0 | 0.0 |
| +2.8 | low | bandit-incremental-cache-control | control | 2026-09-07 | 67.2k | 0 | 0.0 |
| +2.7 | low | bandit-incremental-cache-control | control | 2026-09-08 | 66.9k | 0 | 0.0 |

All 4 flagged trials were unresolved failures. Low-thinking outliers are
runaway/loop runs: a token count far above the 2-sigma band predicts a
failed run before the verdict lands. No high-thinking trial breached
its task norm.

### Fallback norm for tasks with few samples

When a task has n < 5, use the pooled relative spread:
sigma_hat = CV_pooled * task_mean.

| thinking | CV median | CV p75 | CV p90 | guidance for 1-sigma band |
|---|---|---|---|---|
| low | 0.15 | 0.21 | 1.63 | mean +/- 0.15*mean |
| high | 0.07 | 0.09 | 0.11 | mean +/- 0.07*mean |

High thinking is intrinsically consistent (median CV ~0.07): even one
test run more than ~30% off the task mean is unusual. Low thinking is
heavier tailed (median CV ~0.15, runaway runs up to CV 2+): use ~2x the
median CV before flagging. The low p90 of 1.63 is the runaway tail, not
steady-state spread - do not size normal bands from it.

### Interpretation

- Usable norm, honest caveat: per-task sigma at n < 5 is provisional.
  The tables are a living baseline; re-run the analysis script after new
  experiments and the bands tighten on their own.
- Immediate triage rule for a single new run: output outside the task's
  2-sigma band (or the fallback band) plus unresolved status = runaway;
  reasoning tokens off by an order of magnitude = thinking-level misconfig.
- The low-thinking failure mode is spending (66-68k output loops on
  tasks whose norm is 3-10k); the high-thinking failure mode has not
  shown up in tokens at all.

### Reproduce

```bash
python3 .agents/artifacts/token_stats_analysis.py
# rewrites .agents/artifacts/token-analysis-gpt-5.6-luna.md
# from ~/.local/share/roastmyharness/runs
```

---



## Snoop fixed retrieval (Phases A-E) vs historic arms

### Headlines

- ipython is the cleanest retrieval win: the old packet completely lacked the history files the task needs; both fixed packets have them all. Beats control too.
- fastapi win is retrieval-plausible in rep 2 (translations gone, key files present); rep 1 won without calling snoop once, so that's agent variance.
- boa and bandit exonerate the retrieval changes: boa fails the same single test 16/17 in both fixed runs with no uniformly-lost evidence; bandit's three snoop runs all land 88-89/89 with old and rep-2 failing the identical file-size edge test. Both sit at capability edge - single-test flips, not packet regressions.

### Identity and scope

| field | value |
|---|---|
| New runs | `snoop-fixed-fac02adf` (rep 1, COMPLETE), `snoop-fixed-d231ee72` (rep 2, matrix complete) |
| Historic baseline | `snoop-smoke-b120e547` (CANCELLED, 31 trials: control + old-snoop arms) |
| Spec | `snoop-fixed.toml`: snoop arm only, `control.enabled = false` |
| Snoop code | Phases A-E (per-commit cap, locale collapse, lane + history fill gating); Debian-12 binary rebuilt post-change |
| Model / thinking | `gpt-5.6-luna` / high, all arms |
| Tasks | 14 paired tasks where both historic arms resolved (plus 4 probe artifacts in rep 2, excluded) |
| Date | 2026-09-09 |

### Results

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

### Paired flips (fixed runs vs old-snoop)

- Rescued in both replicates: fastapi, claude-code, ipython.
- Lost in both replicates: boa.
- Split between replicates (variance, no call): bandit, kcp, cattrs, etree, kombu-single.
- Stable: arcane, httpx (pass); clack, kgateway, kombu-dead (fail).

### Discordant-pair deep dive

Packet comparison across old-snoop, fixed-r1, fixed-r2 (queries, item kinds,
commit shas, code locators) plus verifier reports:

- **ipython, retrieval-attributable win.** Old packet lacks `history.py`,
  `historyapp.py`, `magics/history.py`, `test_history.py` - the exact files
  the task needs. Both fixed packets contain all of them. Old had
  `aaa5a456x3`, fixed runs cap at x2. Control failed too, so fixed snoop
  beats control here.
- **fastapi, retrieval-plausible win (rep 2).** Old q1 packet: 8 translated
  `first-steps.md` siblings plus a x3 commit. Rep-2 q1: English-only docs,
  no x3, `routing.py`/`models.py`/security files present. Rep-1 won with
  zero `context` calls, so that replicate is agent path variance, not snoop.
- **claude-code, weak-positive.** No x3 recurrence in fixed runs
  (old had `6a9c59a5x3`), but agent queries diverged across all three
  trials, so the win cannot be isolated to retrieval.
- **boa, no retrieval attribution.** No commit is uniformly lost across the
  fixed packets (empty set diff); code composition is similar. Both fixed
  runs produce ~29KB patches failing the SAME single test 16/17
  (`cancelled_session_jobs_are_skipped_but_unrelated_jobs_still_run`).
  Task at capability edge; historic pass likely luck.
- **bandit, edge-test noise.** All three snoop runs reach 88-89/89; old and
  rep-2 fail the IDENTICAL single test
  (`test_cache_stats_shows_cache_file_size_bytes`, file-size-in-bytes);
  rep-1 passes fully. Not a retrieval story.

### Interpretation

Fixed retrieval converts three historic snoop fails to stable passes with
packet-level mechanisms (missing key files surfacing, translation flood and
x3 duplicates gone), holds code/docs recall at zero locator losses (A5
replay), and shows no packet-attributable loss on boa/bandit - both sit at
capability edge with single-test flips. Remaining variance (split
replicates) dominates small-n reading; don't rank on totals.

### Incidents affecting the numbers

- The 30-task `snoop-fixed` launch was SIGTERMed on its wrapper instead of
  the runner; the runner survived and lazily enumerated the by-then-narrowed
  14-task spec, completing as `fac02adf` (rep 1). Rep 2 (`d231ee72`) ran the
  same 14 plus 4 probe artifacts (arktype/optique errors, mobly/onedump
  probe fails from probe retries), excluded above.
- Both fixed runs executed concurrently with each other and with unrelated
  swe experiments; CPU contention may inflate split-replicate noise.
- Stale `fac02adf`-era runner lingered post-COMPLETE and was reaped; no
  trials affected.

---



## Claude Code bare vs Pi bare

First harness-only comparison: both arms ran the SAME model at the
SAME thinking level, so any difference isolates the harness, not the
model.

### Identity

| field | value |
|---|---|
| Run id | `pi-vs-claude-bare-ed2d5b93` |
| Date | 2026-09-09 |
| Spec | `pi-vs-claude-bare.toml` |
| Branch | `ad-hoc/claude-bare` (Claude arm support restored from `40f6383`) |
| Harness | roastmyharness 0.1.0, pier 0.3.1 |
| Model (both arms) | `anthropic-gateway/claude-opus-5` |
| Thinking (both arms) | `low` |
| Pi arm | pi 0.85.1 (`pi_version = "latest"` at prepare) |
| Claude arm | Claude Code 2.1.266 (npm-pinned) |
| Task corpus | bundled DeepSWE, preset `luna-signal`, catalog rev 2026-09 |
| Task hash | `90592907baf82f7bcd84c4eccce0b1229c63711f0e6e8ecf19c362ce01b6f7a0` |
| Run dir | `~/.local/share/roastmyharness/runs/pi-vs-claude-bare-ed2d5b93` |

### Design

Smoke scale: one task, one repetition per arm, concurrency 1.

Task: `boa-hierarchical-evaluation-cancellation` (DeepSWE, medium
difficulty band). Rust work in the boa_engine JS engine: implement
evaluation cancellation semantics (cancel handles, job skipping,
parent/child reason propagation) behind a verifier of 24 cargo-nextest
tests - 17 fail-to-pass and 7 pass-to-pass. The task grades to 1.0
only when all 24 pass.

Fairness contracts (bare means bare):

| arm | contract |
|---|---|
| pi control | `-nc --no-skills --no-prompt-templates --no-themes`, no extensions, no skills |
| claude | `--strict-mcp-config`, pinned settings.json (`bypassPermissions`, telemetry/auto-update off), no MCP servers |

Both arms ran the identical instruction, container image, egress
allowlist (model gateway + npm), and git identity.

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

Paired flips (claude vs control): 1 broken flip - claude passed the
task pi failed. No rescued flips, no both-pass.

### Pi's near miss

Pi scored 23/24. Every pass-to-pass test held (no regressions), and 16
of 17 fail-to-pass tests flipped green. One fail-to-pass test stayed
red:

```
[f2p] boa_engine: tests::evaluation::
  cancelled_session_jobs_are_skipped_but_unrelated_jobs_still_run
```

The semantic gap is precise: pi's implementation got cancellation to
stop execution, propagate reasons to children, and reject enqueueing
onto cancelled handles - but its job-skipping path did not preserve
the "unrelated jobs still run" invariant when a session is cancelled.
Under DeepSWE's all-or-nothing verifier that is reward 0.0 despite a
~96% correct implementation. This is exactly the near-miss band the
medium difficulty label targets, and it is the most useful single
data point of the smoke: the arms differ by one semantic edge case,
not by capability.

Claude Code passed all 17 fail-to-pass and all 7 pass-to-pass tests.

### Behavioral notes (n=1, anecdotal)

- Claude burned ~2x pi's cached input (5.1M vs 2.7M) and took 6
  minutes longer; thinking contributed 15.8k of its 50k output.
- Pi's failure mode was semantic (one edge case), not mechanical: no
  errors, no empty patch, no timeout.
- Claude's transcript shows heartbeat keep-alives during long rust
  builds; wall-time comparisons on build-heavy tasks carry that noise.

### Telemetry provenance (incident disclosure)

The claude arm's token/cost cells initially reported zero. Cause:
Claude Code writes its transcript and `.claude.json` mode 0600/0700
as the container user; pier's host-side relocate preserved that, so
the ATIF converter failed with PermissionError and silently dropped
all metrics. Fixed in `f628ec7` (scan tolerance) and `c8a134a`
(container-side chmod after agent exit). The numbers above were then
recovered from this same trial's artifacts: transcript converted with
pier's own ATIF converter, metrics folded into `result.json`, report
regenerated. Nothing was re-run; the pass/fail verdicts were never in
question. Cost is Claude Code's list-price accounting; actual gateway
cost differs.

### Interpretation

- n=1: no ranking signal. The single paired flip is consistent with
  harness differences in prompt wiring, tool ergonomics, or luck.
- Next step when resuming this experiment: set `include = ["*"]` in
  `pi-vs-claude-bare.toml` for the 30-task `luna-signal` screen,
  `per_variant = 2`. Expect roughly $140-200 list-price for the
  claude arm at this per-task cost.
- Watch item: whether pi's near-miss pattern (many tests green, one
  semantic edge case red) repeats on other tasks; all-or-nothing
  verifiers punish it disproportionately.

### Reproduce

```bash
roastmyharness validate pi-vs-claude-bare.toml
roastmyharness run pi-vs-claude-bare.toml
roastmyharness watch <run-id>
```

Requires `GATEWAY_API_KEY` exported, docker running, and the
`ad-hoc/claude-bare` branch (Claude arm support is not on main).

---



## Pi bare vs OMP bare (partial, cancelled)

The first cross-agent comparison in this log, and the run that drove
omp support (`1d285a6`). Both arms ran the SAME model at the SAME
thinking level, so arm differences isolate the harness plumbing. The
run was cancelled partway, so the omp column is partial - disclosed
here rather than dropped.

### Identity

| field | value |
|---|---|
| Run id | `omp-9task-53a89fc3` (9-task) + `omp-live-smoke-16a9f2f7` (smoke) |
| Date | 2026-08-28 |
| Harness | roastmyharness 0.1.0, pier 0.3.x, schema v1 (pre-catalog, pre-bundled corpus) |
| Model (both arms) | `gpt-5.6-luna` (same hosted gateway as every other section) |
| Thinking (both arms) | `high` |
| Pi arm | pi 0.84.3 |
| OMP arm | oh-my-pi (omp) 18.0.9, first omp support (`1d285a6`) |
| Task corpus | DeepSWE via external DSE-tests checkout (pre-bundling), 9 hand-picked tasks |
| Status | CANCELLED at 20:27 UTC; control finished 7/9, omp 4/9 |
| Run dir | `~/.roastmyharness/runs/omp-9task-53a89fc3` |

### Design

Nine DeepSWE tasks across TypeScript (awilix, clack, happy-dom), Go
(etree), Python (httpx, ipython), and rendering/parsers (katex),
`per_variant = 2`, bare control enabled, no repetitions. This predates
presets and the catalog: tasks were listed explicitly in the spec.

Fairness contracts (bare means bare):

| arm | contract |
|---|---|
| pi control | `-nc --no-skills --no-prompt-templates --no-themes`, no extensions, no skills |
| omp | `--no-skills` plus staged `config.yml` disabling implicit provider-config auto-load; Bun pinned because task images shadow it |

Both arms ran the identical instruction, container image, egress
allowlist, and git identity.

### Results (4 matched tasks; 3 control-only, 2 never started)

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

Per-task cached-input ratio omp/pi: 2.75x, 6.58x, 3.79x, 2.85x -
omp's context amplification is consistent, not one outlier.

Paired flips (omp vs pi): 1 broken (etree), 0 rescued, 1 both-pass,
2 both-fail.

### The broken flip: etree one test short

omp scored 51/52 on etree-xml-diff-patch and lost the task on a single
fail-to-pass test:

```
[f2p] github.com/beevik/etree.TestMerge3WayStructuralConflict
```

pi passed all 52. Same shape as pi's near miss in the Claude smoke:
an all-or-nothing verifier turns one semantic edge case (three-way
merge conflict resolution here) into reward 0.0 despite a ~98% correct
implementation.

### Both-fail anatomy

- awilix: pi missed exactly one test ("allows scope.initialize()
  without calling parent.initialize"); omp missed that same test plus
  "initialization failure triggers rollback leaves container in failed
  state". Strictly worse by one test, same semantic neighborhood.
- clack: pi missed 3, omp missed 10 - a cluster of AbortController,
  retry, and loading-state semantics ("AbortError from fetch is
  silently swallowed", "loading remains true between retries", ...).
  omp reached 102 steps vs pi's 32 and still left more of the
  cluster red: its extra activity did not convert into coverage.

### The smoke that preceded it

`omp-live-smoke-16a9f2f7` (same day, one task:
ofetch-per-origin-circuit-breaker): both arms PASS 60/60 (47 f2p + 13
p2p). pi 9.5m wall, 1.9M cached input, 42 steps; omp 19.7m wall, 5.2M
cached input (2.7x), 63 steps. Identical verdicts, so the smoke only
validated plumbing - and foreshadowed the token/steps gap the 9-task
run then measured.

### Behavioral notes

- omp's stable signature: ~3x pi's cached input, ~1.9x the steps,
  ~1.2x the output, ~1.7-2.1x the wall time, on every matched task.
  Its session/prompt wiring re-reads far more context per turn; the
  extra turns are mostly verification and re-checking.
- pi's failures were never mechanical: no timeouts, no empty patches,
  no infra errors on either arm. Every miss was a semantic test gap.
- All four arms' failures sit in async-lifecycle semantics (abort,
  retry, rollback, cleanup) - the shared model likely drives the
  shared blind spots; the harness shapes only how far it gets.

### Telemetry provenance

The run was cancelled before finalize, so no summary/report was
generated at run time. All numbers here were reconciled 2026-09-09
from the raw trial artifacts (result.json, verifier/ctrf.json,
verifier/reward.json) with `.agents/artifacts/omp_deep_dive.py`; wall
times come from result.json started_at/finished_at. Nothing was
re-run; pass/fail verdicts were never in question. Gaps at this
harness vintage, disclosed: reasoning-token split and tool-call counts
were not captured by the adapter payload, and `peak_context_tokens`
is unusable (same telemetry issue as the token-baseline section).

### Interpretation

- n=4 matched: no ranking signal, and the cancelled tail means the
  omp arm never saw 5 of 9 tasks. Treat this as a plumbing-validating
  first cross-agent run with one usable paired flip.
- The robust finding is cost shape, not outcomes: omp trades ~3x
  context and ~2x wall time for activity that did not convert into
  extra passes here - and its one extra missed test (etree) decided
  the only flip.
- The near-miss band (one red test on an all-or-nothing verifier) has
  now appeared in both cross-agent smokes; it is the recurring shape
  of harness differences at this scale.
- If resumed today: rebuild the spec on schema v2 against the bundled
  corpus with `preset = "luna-signal"` and `repetitions >= 1`; the
  stored run's identity hashes are not comparable to current runs.

### Reproduce

```bash
python3 .agents/artifacts/omp_deep_dive.py   # per-trial metrics + flips
roastmyharness status omp-9task-53a89fc3     # matrix (from the merged DB)
```

Run data lives under `~/.roastmyharness/runs/`; the DB row was
migrated with run_dir rewritten, so status/report work from the new
home.

---



## Adding a new experiment section

Append above the oldest section. Minimum fields: run id, date, spec
file, branch/harness versions, model and thinking per arm, task
corpus and scale, results table, paired flips, interpretation, and
any incident affecting the numbers. Disclose telemetry gaps and
post-hoc recoveries inline - never silently.
