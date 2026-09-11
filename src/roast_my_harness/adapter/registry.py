"""Pi runtime facts: the only runtime RoastMyHarness launches.

Plain constants (no registry: there is exactly one adapter,
adapter/pi_agent.py). Stdlib-only: spec loading and the runner use this
anywhere, and adapters load inside pier's venv.
"""

from __future__ import annotations

from roast_my_harness.constants import DEFAULT_PI_VERSION, FAIRNESS_FLAGS

PI_IMPORT_PATH = "roast_my_harness.adapter.pi_agent:PiAgent"
PI_NPM_PACKAGE = "@earendil-works/pi-coding-agent"
PI_BINARY = "pi"
PI_HOME_ENV = "PI_CODING_AGENT_DIR"
PI_VERSION_FIELD = "pi_version"
PI_FAIRNESS_FLAGS = FAIRNESS_FLAGS
PI_DEFAULT_VERSION = DEFAULT_PI_VERSION
