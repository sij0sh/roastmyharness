BigQuery pipe syntax chains transformations via `|>` instead of nested clauses. The formatter lacks pipe awareness, misformatting pipe queries. Implement the core: `FROM` followed by `|> SELECT` (including `SELECT *`), `|> WHERE`, `|> ORDER BY`, and `|> LIMIT`, chained in any combination. Function calls inside steps must work. Pipe queries nest inside parentheses as subqueries, can be followed by semicolons, can sit next to traditional statements in one session, and a traditional query plus a pipe query format independently in the same session.

Mechanics note: the parser is generated -- after editing `src/parser/grammar.ne`, regenerate it with `./node_modules/.bin/nearleyc src/parser/grammar.ne -o src/parser/grammar.ts` (also the `yarn grammar` script) before running anything. Do not commit generated files (`src/parser/grammar.ts` is rebuilt automatically wherever it is needed).

Layout conventions: pipe queries start with standalone `FROM` and each subsequent `|>` step occupies its own line at base indentation. The pipe operator and clause keyword share the same line. Clauses that take expression lists place their body on the next line, indented one level deeper, following the same pattern the formatter already uses for that clause type in traditional queries. One-line clauses keep their content on the same line as the keyword. `keywordCase` governs all pipe keywords. `|>` must tokenize as a distinct type, not bitwise `|` plus `>`.

Examples (input formats to output):

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

Verify before you finish: run the formatter on every example input below and match the output exactly; run `npx jest test/bigquery.test.ts --silent --no-coverage`, and the full `npx jest --silent --no-coverage` before committing (keep runs quiet -- rely on failure summaries, not full logs). Then commit your work (`git commit -m "stage-X ..."`). The held-out acceptance tests check exactly these behaviors and layouts.