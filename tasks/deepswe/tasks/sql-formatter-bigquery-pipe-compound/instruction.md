BigQuery pipe syntax chains transformations via `|>` instead of nested clauses. The formatter lacks pipe awareness, misformatting pipe queries. Build pipe support in four phases in this session, without resetting. Each phase depends on the previous ones: later phases reuse the tokenizer, grammar, and layout machinery you build now, and all earlier phases must keep working at the end. Do not finish until all four phases are implemented, committed, and verified — a partial submission with fewer than four phase commits is a failure.

Two mechanics notes first: the parser is generated — after editing `src/parser/grammar.ne`, regenerate it with `./node_modules/.bin/nearleyc src/parser/grammar.ne -o src/parser/grammar.ts` (also the `yarn grammar` script) before running anything. And verify with the repo's own visible suite: run `npx jest test/bigquery.test.ts --no-coverage` after each phase, and the full `npx jest --no-coverage` before every commit, to catch regressions in the existing dialects.

Each phase below lists every behavior you must implement as input/output examples. Before committing a phase, run the formatter on every one of its example inputs and confirm your output matches exactly. The held-out acceptance tests check exactly these behaviors and layouts.

After finishing each phase and its verification, commit that phase separately (`git commit -m "phase-A ..."`, etc.) before starting the next. At the very end run the full suite once more to confirm nothing regressed.

Layout conventions for every phase: pipe queries start with standalone `FROM` and each subsequent `|>` step occupies its own line at base indentation. The pipe operator and clause keyword share the same line. Clauses that take expression lists (`WHERE`, `SELECT`, `ORDER BY`, `AGGREGATE`, `EXTEND`, `SET`, `DROP`, `RENAME`) place their body on the next line, indented one level deeper, following the same pattern the formatter already uses for that clause type in traditional queries. One-line clauses (`LIMIT`, `JOIN` and its variants, `AS`, `PIVOT`, `UNPIVOT`, `TABLESAMPLE`) keep their content on the same line as the keyword. `keywordCase` governs all pipe keywords. `|>` must tokenize as a distinct type, not bitwise `|` plus `>`.

## Phase A — core pipe steps

`FROM` followed by `|> SELECT` (including `SELECT *`), `|> WHERE`, `|> ORDER BY`, and `|> LIMIT`, chained in any combination. Function calls inside steps must work. Pipe queries nest inside parentheses as subqueries, can be followed by semicolons, can sit next to traditional statements in one session, and a traditional query plus a pipe query format independently in the same session.

`FROM orders |> WHERE status = 'shipped'` formats to:

```
FROM
  orders
|> WHERE
  status = 'shipped'
```

`FROM orders |> SELECT order_id, customer_id, amount` formats to:

```
FROM
  orders
|> SELECT
  order_id,
  customer_id,
  amount
```

`FROM orders |> SELECT *` formats to:

```
FROM
  orders
|> SELECT
  *
```

`FROM orders |> WHERE status = 'shipped' |> SELECT customer_id, amount |> ORDER BY amount DESC |> LIMIT 10` formats to:

```
FROM
  orders
|> WHERE
  status = 'shipped'
|> SELECT
  customer_id,
  amount
|> ORDER BY
  amount DESC
|> LIMIT 10
```

`FROM orders |> ORDER BY created_at DESC, order_id ASC` formats to:

```
FROM
  orders
|> ORDER BY
  created_at DESC,
  order_id ASC
```

`FROM orders |> LIMIT 25` formats to:

```
FROM
  orders
|> LIMIT 25
```

`FROM orders |> WHERE TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), created_at, DAY) < 30 |> SELECT order_id` formats to:

```
FROM
  orders
|> WHERE
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), created_at, DAY) < 30
|> SELECT
  order_id
```

`FROM (FROM orders |> WHERE status = 'shipped') |> SELECT customer_id` formats to:

```
FROM
  (
    FROM
      orders
    |> WHERE
      status = 'shipped'
  )
|> SELECT
  customer_id
```

`SELECT a FROM t; FROM orders |> WHERE status = 'shipped' |> SELECT order_id` formats to:

```
SELECT
  a
FROM
  t;

FROM
  orders
|> WHERE
  status = 'shipped'
|> SELECT
  order_id
```

`SELECT 1; FROM orders |> WHERE status = 'shipped' |> LIMIT 5;` formats to:

```
SELECT
  1;

FROM
  orders
|> WHERE
  status = 'shipped'
|> LIMIT 5;
```

`FROM orders |> WHERE status = 'shipped' |> LIMIT 10;` formats to:

```
FROM
  orders
|> WHERE
  status = 'shipped'
|> LIMIT 10;
```

## Phase B — pipe-exclusive clauses

`|> AGGREGATE` with an optional nested `GROUP BY` sub-clause at its own indentation level (also without `GROUP BY`, and with multiple expressions / grouping columns). `|> EXTEND` for computed columns (single, multiple, and followed by further steps). `|> SET`, `|> DROP`, `|> AS` for naming intermediates. Pipe `JOIN` and `LEFT JOIN` with `ON` conditions. Bitwise `|` inside expressions must not be mistaken for a pipe step. `keywordCase` upper/lower applies to every pipe keyword. Finish with the end-to-end chain.

`FROM orders |> AGGREGATE SUM(amount) AS total GROUP BY customer_id` formats to:

```
FROM
  orders
|> AGGREGATE
  SUM(amount) AS total
  GROUP BY
    customer_id
```

`FROM orders |> AGGREGATE SUM(amount) AS total, COUNT(*) AS cnt GROUP BY customer_id, region` formats to:

```
FROM
  orders
|> AGGREGATE
  SUM(amount) AS total,
  COUNT(*) AS cnt
  GROUP BY
    customer_id,
    region
```

`FROM orders |> AGGREGATE COUNT(*) AS total_orders` formats to:

```
FROM
  orders
|> AGGREGATE
  COUNT(*) AS total_orders
```

`FROM orders |> EXTEND amount * 1.1 AS amount_with_tax` formats to:

```
FROM
  orders
|> EXTEND
  amount * 1.1 AS amount_with_tax
```

`FROM orders |> EXTEND amount * 1.1 AS amount_with_tax, amount * 0.1 AS tax_amount` formats to:

```
FROM
  orders
|> EXTEND
  amount * 1.1 AS amount_with_tax,
  amount * 0.1 AS tax_amount
```

`FROM orders |> EXTEND amount * 1.1 AS total |> WHERE total > 100 |> SELECT customer_id, total` formats to:

```
FROM
  orders
|> EXTEND
  amount * 1.1 AS total
|> WHERE
  total > 100
|> SELECT
  customer_id,
  total
```

`FROM orders |> SET status = 'processed', updated_at = CURRENT_TIMESTAMP()` formats to:

```
FROM
  orders
|> SET
  status = 'processed',
  updated_at = CURRENT_TIMESTAMP()
```

`FROM orders |> DROP internal_id, debug_flag` formats to:

```
FROM
  orders
|> DROP
  internal_id,
  debug_flag
```

`FROM orders |> JOIN customers ON orders.customer_id = customers.id` formats to:

```
FROM
  orders
|> JOIN customers ON orders.customer_id = customers.id
```

`FROM orders |> LEFT JOIN customers ON orders.customer_id = customers.id` formats to:

```
FROM
  orders
|> LEFT JOIN customers ON orders.customer_id = customers.id
```

`FROM orders |> AS o |> WHERE o.status = 'shipped'` formats to:

```
FROM
  orders
|> AS o
|> WHERE
  o.status = 'shipped'
```

`from orders |> where status = 'shipped' |> aggregate count(*) as total group by customer_id` with options `{keywordCase: 'upper', functionCase: 'upper'}` formats to:

```
FROM
  orders
|> WHERE
  status = 'shipped'
|> AGGREGATE
  COUNT(*) AS total
  GROUP BY
    customer_id
```

`FROM orders |> WHERE status = 'shipped' |> LIMIT 10` with options `{keywordCase: 'lower'}` formats to:

```
from
  orders
|> where
  status = 'shipped'
|> limit 10
```

`FROM t |> WHERE a | b > 0 |> LIMIT 5` formats to:

```
FROM
  t
|> WHERE
  a | b > 0
|> LIMIT 5
```

`FROM orders |> WHERE status = 'shipped' |> AGGREGATE SUM(amount) AS total, COUNT(*) AS cnt GROUP BY customer_id |> ORDER BY total DESC |> LIMIT 10` formats to:

```
FROM
  orders
|> WHERE
  status = 'shipped'
|> AGGREGATE
  SUM(amount) AS total,
  COUNT(*) AS cnt
  GROUP BY
    customer_id
|> ORDER BY
  total DESC
|> LIMIT 10
```

## Phase C — further pipe operators

Extend the Phase A/B machinery (same tokenizer promotion, same grammar dispatch, same step layout) to four more real pipe operators. `|> RENAME old AS new` (single and comma-separated, chainable, honors `keywordCase`). `|> PIVOT (agg FOR col IN (...))` and `|> UNPIVOT (val FOR key IN (...))`, formats like the traditional PIVOT operator: keyword and opening paren on the step line, arguments indented. `|> TABLESAMPLE SYSTEM (n)` / `(n PERCENT)`, one line like `LIMIT`. Chaining must work in both directions (e.g. PIVOT followed by WHERE). Previous phases' behavior must not change.

`FROM t |> RENAME a AS b` formats to:

```
FROM
  t
|> RENAME
  a AS b
```

`FROM t |> RENAME a AS b, c AS d` formats to:

```
FROM
  t
|> RENAME
  a AS b,
  c AS d
```

`FROM t |> WHERE x > 1 |> RENAME a AS b |> SELECT b` formats to:

```
FROM
  t
|> WHERE
  x > 1
|> RENAME
  a AS b
|> SELECT
  b
```

`from t |> rename a as b` with options `{keywordCase: 'lower'}` formats to:

```
from
  t
|> rename
  a as b
```

`FROM Produce |> PIVOT (SUM(sales) FOR quarter IN (Q1, Q2, Q3, Q4))` formats to:

```
FROM
  Produce
|> PIVOT (
  SUM(sales)
  FOR quarter IN (Q1, Q2, Q3, Q4)
)
```

`FROM Produce |> UNPIVOT (sales FOR quarter IN (Q1, Q2))` formats to:

```
FROM
  Produce
|> UNPIVOT (
  sales
  FOR quarter IN (Q1, Q2)
)
```

`FROM dataset.my_table |> TABLESAMPLE SYSTEM (10 PERCENT)` formats to:

```
FROM
  dataset.my_table
|> TABLESAMPLE SYSTEM (10 PERCENT)
```

`FROM Produce |> PIVOT (SUM(sales) FOR quarter IN (Q1, Q2)) |> WHERE Q1 > 0` formats to:

```
FROM
  Produce
|> PIVOT (
  SUM(sales)
  FOR quarter IN (Q1, Q2)
)
|> WHERE
  Q1 > 0
```

## Phase D — integration and compatibility

Pipe queries everywhere traditional queries are accepted: as CTE bodies, as `UNION ALL` branches, in `CREATE VIEW v AS ...`, in `INSERT INTO t (...)`, and as `JOIN (...)` subqueries — including the Phase C operators in those positions (RENAME in a CTE, PIVOT under CREATE VIEW, UNION of two RENAME queries). Query parameters (`?` with `params`) must substitute inside pipe chains. Line comments must survive around pipe steps. Under `indentStyle: tabularLeft` / `tabularRight`, pipe steps keep the standard pipe layout (the `|>` prefix already aligns them; tabular keyword padding does not apply to pipe keywords) while the rest of the query follows the usual tabular rules.

`WITH x AS (FROM t |> RENAME a AS b) SELECT b FROM x` formats to:

```
WITH
  x AS (
    FROM
      t
    |> RENAME
      a AS b
  )
SELECT
  b
FROM
  x
```

`FROM a |> RENAME x AS y UNION ALL FROM b |> RENAME x AS y` formats to:

```
FROM
  a
|> RENAME
  x AS y
UNION ALL
FROM
  b
|> RENAME
  x AS y
```

`CREATE VIEW v AS FROM Produce |> PIVOT (SUM(sales) FOR quarter IN (Q1, Q2))` formats to:

```
CREATE VIEW v AS
FROM
  Produce
|> PIVOT (
  SUM(sales)
  FOR quarter IN (Q1, Q2)
)
```

`INSERT INTO t (FROM orders |> EXTEND amount * 1.1 AS total |> SELECT total)` formats to:

```
INSERT INTO
  t (
    FROM
      orders
    |> EXTEND
      amount * 1.1 AS total
    |> SELECT
      total
  )
```

`FROM orders |> AGGREGATE SUM(amount) AS total GROUP BY customer_id |> WHERE total > ?` with options `{params: ['100']}` formats to:

```
FROM
  orders
|> AGGREGATE
  SUM(amount) AS total
  GROUP BY
    customer_id
|> WHERE
  total > 100
```

`FROM t |> EXTEND a + b AS c -- computed\n|> WHERE c > 1` formats to:

```
FROM
  t
|> EXTEND
  a + b AS c -- computed
|> WHERE
  c > 1
```

`FROM a |> JOIN (FROM b |> SET x = 1 |> SELECT x) AS bb ON a.id = bb.id` formats to:

```
FROM
  a
|> JOIN (
  FROM
    b
  |> SET
    x = 1
  |> SELECT
    x
) AS bb ON a.id = bb.id
```

`FROM orders |> WHERE x = 1 |> SELECT a` with options `{indentStyle: 'tabularLeft',}` formats to:

```
FROM      orders
|> WHERE
          x = 1
|> SELECT
          a
```

`FROM orders |> WHERE x = 1 |> SELECT a` with options `{indentStyle: 'tabularRight',}` formats to:

```
     FROM orders
|> WHERE
          x = 1
|> SELECT
          a
```

`FROM t |> RENAME a AS b |> LIMIT 5` with options `{indentStyle: 'tabularLeft',}` formats to:

```
FROM      t
|> RENAME
          a AS b
|> LIMIT 5
```

IMPORTANT: Please work on this in a new branch from main and commit everything when you are done (one commit per phase as described above).
