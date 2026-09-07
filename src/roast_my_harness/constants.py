"""Single-home values shared across the pier import boundary.

Stdlib-only: spec, adapter, and CLI all import this module, and the
adapter loads inside pier's venv, so this module must never pull a
third-party dependency.
"""

from __future__ import annotations

# Default pi coding-agent npm pin when a spec does not pin one:
# "latest" resolves to the newest release every time we run.
DEFAULT_PI_VERSION = "latest"

# Provider name of pi's built-in Codex authentication.
CODEX_PROVIDER = "openai-codex"

# Process exit code by final experiment state; anything else exits 0.
EXIT_CODES = {"FAILED": 2, "CANCELLED": 3}

# Fairness flags kept identical for every arm so repo/global context files
# and per-variant cosmetics cannot differ.
FAIRNESS_FLAGS = "--no-skills --no-prompt-templates --no-themes -nc"

# Deterministic git identity configured in every agent container at setup.
# Agents are instructed to commit their work, and patch collection diffs the
# working tree, but a commit still fails without user.name/user.email (and on
# dubious-ownership checkouts). A fixed identity keeps commits working the
# same way on every trial instead of depending on image bake state.
GIT_IDENTITY_NAME = "roastmyharness"
GIT_IDENTITY_EMAIL = "roastmyharness@local"
