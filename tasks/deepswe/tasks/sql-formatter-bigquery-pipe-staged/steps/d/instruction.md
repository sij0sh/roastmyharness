Final integration round. Downstream consumers now require pipe queries everywhere traditional queries are accepted: as CTE bodies, as `UNION ALL` branches, in `CREATE VIEW v AS ...`, in `INSERT INTO t (...)`, and as `JOIN (...)` subqueries -- including the newer operators in those positions (RENAME in a CTE, PIVOT under CREATE VIEW, UNION of two RENAME queries). Query parameters (`?` with `params`) must substitute inside pipe chains. Line comments must survive around pipe steps. Under `indentStyle: tabularLeft` / `tabularRight`, pipe steps keep the standard pipe layout (the `|>` prefix already aligns them; tabular keyword padding does not apply to pipe keywords) while the rest of the query follows the usual tabular rules. Everything from the earlier stages must keep working.

Layout conventions: pipe queries start with standalone `FROM` and each subsequent `|>` step occupies its own line at base indentation. The pipe operator and clause keyword share the same line. Clauses that take expression lists place their body on the next line, indented one level deeper, following the same pattern the formatter already uses for that clause type in traditional queries. One-line clauses keep their content on the same line as the keyword. `keywordCase` governs all pipe keywords. `|>` must tokenize as a distinct type, not bitwise `|` plus `>`.

Examples:

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

Verify before you finish: run the formatter on every example input below and match the output exactly; run `npx jest test/bigquery.test.ts --silent --no-coverage`, and the full `npx jest --silent --no-coverage` before committing (keep runs quiet -- rely on failure summaries, not full logs). Then commit your work (`git commit -m "stage-X ..."`). The held-out acceptance tests check exactly these behaviors and layouts.

IMPORTANT: Please work on this in a new branch from main and commit everything when you are done.