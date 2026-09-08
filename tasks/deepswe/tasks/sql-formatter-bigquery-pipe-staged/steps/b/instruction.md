Thanks -- the core pipe support from the previous step is in place. Now extend it with the pipe-exclusive clauses, preserving everything already working: `|> AGGREGATE` with an optional nested `GROUP BY` sub-clause at its own indentation level (also without `GROUP BY`, and with multiple expressions / grouping columns). `|> EXTEND` for computed columns (single, multiple, and followed by further steps). `|> SET`, `|> DROP`, `|> AS` for naming intermediates. Pipe `JOIN` and `LEFT JOIN` with `ON` conditions. Bitwise `|` inside expressions must not be mistaken for a pipe step. `keywordCase` upper/lower applies to every pipe keyword. Finish with the end-to-end chain.

Layout conventions: pipe queries start with standalone `FROM` and each subsequent `|>` step occupies its own line at base indentation. The pipe operator and clause keyword share the same line. Clauses that take expression lists place their body on the next line, indented one level deeper, following the same pattern the formatter already uses for that clause type in traditional queries. One-line clauses keep their content on the same line as the keyword. `keywordCase` governs all pipe keywords. `|>` must tokenize as a distinct type, not bitwise `|` plus `>`.

Examples:

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

Verify before you finish: run the formatter on every example input below and match the output exactly; run `npx jest test/bigquery.test.ts --silent --no-coverage`, and the full `npx jest --silent --no-coverage` before committing (keep runs quiet -- rely on failure summaries, not full logs). Then commit your work (`git commit -m "stage-X ..."`). The held-out acceptance tests check exactly these behaviors and layouts.