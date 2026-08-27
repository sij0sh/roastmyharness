"""Typed JSON responses for the agent orchestration contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Question(_Response):
    """One missing decision the caller must resolve before proceeding."""

    field: str
    message: str
    choices: list[str] = []


class ExperimentSummary(_Response):
    tasks: int
    arms: int
    trials: int
    max_parallel: int
    model: str


class PrepareResult(_Response):
    ok: bool
    state: Literal["ready_for_confirmation", "needs_input"]
    plan_id: str | None = None
    spec_path: str | None = None
    experiment: ExperimentSummary | None = None
    warnings: list[str] = []
    questions: list[Question] = []
    next_action: str | None = None


class StartResult(_Response):
    ok: bool
    state: Literal["running", "already_started"]
    plan_id: str
    experiment_id: str
    started: bool
    next_action: str = "status"


class ReportPaths(_Response):
    markdown: str
    csv: str


class StatusResult(_Response):
    ok: bool
    experiment_id: str
    state: str
    final: bool
    tasks: list[str]
    matrix: dict[str, dict[str, str]]
    totals: dict[str, dict[str, int]]
    report: ReportPaths | None


class CancelResult(_Response):
    ok: bool
    experiment_id: str
    state: str
    cancelled: bool
    note: str | None = None


class ReportResult(_Response):
    ok: bool
    experiment_id: str
    csv_path: str
    markdown_path: str
