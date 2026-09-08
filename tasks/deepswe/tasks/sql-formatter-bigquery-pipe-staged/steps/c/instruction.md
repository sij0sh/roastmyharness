The team has decided the pipe support must cover four more real pipe operators, reusing the tokenizer, grammar dispatch, and step layout you already built: `|> RENAME old AS new` (single and comma-separated, chainable, honors `keywordCase`). `|> PIVOT (agg FOR col IN (...))` and `|> UNPIVOT (val FOR key IN (...))`, formatted like the traditional PIVOT operator: keyword and opening paren on the step line, arguments indented. `|> TABLESAMPLE SYSTEM (n)` / `(n PERCENT)`, one line like `LIMIT`. Chaining must work in both directions (e.g. PIVOT followed by WHERE). All previously implemented behavior must keep working.

Layout conventions: pipe queries start with standalone `FROM` and each subsequent `|>` step occupies its own line at base indentation. The pipe operator and clause keyword share the same line. Clauses that take expression lists place their body on the next line, indented one level deeper, following the same pattern the formatter already uses for that clause type in traditional queries. One-line clauses keep their content on the same line as the keyword. `keywordCase` governs all pipe keywords. `|>` must tokenize as a distinct type, not bitwise `|` plus `>`.

Examples:

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

Verify before you finish: run the formatter on every example input below and match the output exactly; run `npx jest test/bigquery.test.ts --silent --no-coverage`, and the full `npx jest --silent --no-coverage` before committing (keep runs quiet -- rely on failure summaries, not full logs). Then commit your work (`git commit -m "stage-X ..."`). The held-out acceptance tests check exactly these behaviors and layouts.