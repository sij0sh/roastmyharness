# Staged task: instructions arrive per step, not here.

This task runs as four sequential same-session stages. The agent never
sees this file as a prompt; pier sends one stage prompt per step from
`steps/<name>/instruction.md`, each after the previous run settles, with
no context reset, repo reset, or new agent between stages:

- steps/a: pipe core (FROM + SELECT/WHERE/ORDER BY/LIMIT steps)
- steps/b: pipe-exclusive clauses (AGGREGATE, EXTEND, SET, DROP, AS, JOINs)
- steps/c: further operators (RENAME, PIVOT, UNPIVOT, TABLESAMPLE)
- steps/d: integration + compatibility (CTE/UNION/VIEW/INSERT, params,
  comments, tabular styles)

Verification is the shared full suite every step (44 phase buckets +
5709 P2P); the trial reward is the final step's result.
