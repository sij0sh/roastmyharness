# Vendored from SWE-Interact (scaleapi/SWE-Interact, commit b32f98c).

Source: data/multiturn/deepswe_arcane-drift-detection-baselines
Dataset manifest digest:
  sha256:77d500c59802958f392f4b1025b5ccb69e6952c3e1e66bd4e7f555031efc4293

## Diff vs upstream (intentional, minimal)

1. task.toml top-level `artifacts`: `[]` ->
   `["/logs/artifacts/model.patch"]`. The task's own tests/test.sh
   already writes that path; without the declaration pier does not
   download it and Roast scoring sees no patch. Same declaration the
   staged `sql-formatter-bigquery-pipe-staged` task carries.

2. task.toml `[environment].docker_image` removed. The pinned ECR
image lacks `/usr/local/bin/repo_exec_server.py` (Harbor injects it
via `--force-build` from `environment/Dockerfile`); without it the
main healthcheck (`localhost:8765`) never passes and pier's
`compose up --wait` fails the trial. Without the pin pier builds
main from `environment/Dockerfile`, which clones the repo and
checks out the same pinned base commit. Repo content stays pinned;
only the `mars-base:latest` base floats.

3. `environment/user-server/server.py`: `max_tokens=32768` ->
   `max_completion_tokens=32768` at both litellm call sites. The
official `SIM_USER_MODEL=openai/gpt-5.5` has no deployment on our
gateway (direct probe: `DeploymentNotFound` on both chat/completions
and responses endpoints despite appearing in `/models`), so the
simulator runs `openai/gpt-5.6-luna` instead. Luna rejects `max_tokens`
(`use max_completion_tokens instead`) and litellm does not remap it
for this deployment name. Verified: litellm with tools +
`reasoning_effort=high` + `max_completion_tokens` completes against
the gateway.

4. Ownership hardening (same class as the staged S2 fix): `trap
   'chmod -R a+rwX /logs/agent /logs/artifacts /logs/verifier ...'
   EXIT` at the top of `tests/test.sh` and
   `steps/05_test_handoff/tests/test.sh`, and the 05 finale
   `exec bash /tests/canonical_test.sh` restructured to
   run-then-chmod-then-`exit $rc` (`exec` skips EXIT traps).
   Without this, root-owned verifier outputs break pier's host-side
   per-step relocate with PermissionError (observed r3).

Nothing else changed: 5 steps, FINAL strategy, intermediate
reward-1 verifiers, docker-compose sidecar, MCP server declaration,
timeouts, and instructions are byte-identical to upstream.

## Known fairness caveat (smoke only)

Upstream sets no `network_mode`, so pier runs the agent with
unrestricted egress (model gateway + sidecar both need it).
Single-step DeepSWE tasks run no-network. Tighten only after the
runway screen, and record any change.
