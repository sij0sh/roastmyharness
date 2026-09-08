Write the file `/app/record.json` with exactly this JSON object:

```json
{
  "name": "atlas",
  "version": "2.4.1",
  "tags": ["red", "green", "blue"],
  "enabled": true
}
```

Rules:

- The file must parse as JSON.
- Every key must match exactly: no missing keys, no extra keys.
- `version` must be the exact string `"2.4.1"`.
- `tags` must contain exactly these three strings in this order.

Commit your work with git when done.
