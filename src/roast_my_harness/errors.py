"""Error hierarchy. Every failure carries a class the CLI can display."""

from __future__ import annotations


class RoastMyHarnessError(Exception):
    """Base class for all RoastMyHarness errors."""


class SpecError(RoastMyHarnessError):
    """Experiment spec loading or validation failed."""


class HomeBuildError(RoastMyHarnessError):
    """Building a Pi home failed."""


class AuthError(RoastMyHarnessError):
    """Credential detection, validation, or staging failed."""


class PierError(RoastMyHarnessError):
    """Pier executable lookup, launch, or execution failed."""
