"""Tests for the stdio MCP server: protocol handling and service reuse."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from roast_my_harness.agent import models
from roast_my_harness.agent.service import ServiceError, UnknownPlanError
from roast_my_harness.mcp_server import TOOL_NAME, dispatch, handle_request


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, action: str, *args: Any, **kwargs: Any):
        self.calls.append((action, args, kwargs))
        return action

    def prepare(self, spec_path, *, skip_docker=False):
        self.calls.append(("prepare", (spec_path,), {"skip_docker": skip_docker}))
        return models.PrepareResult(
            ok=True,
            state="ready_for_confirmation",
            plan_id="plan_abc123def456",
            spec_path=str(spec_path),
            experiment=models.ExperimentSummary(
                tasks=2, arms=2, trials=4, max_parallel=2, model="pi/test"
            ),
            next_action="start",
        )

    def start(self, plan_id, *, skip_docker=False):
        self.calls.append(("start", (plan_id,), {"skip_docker": skip_docker}))
        if plan_id == "plan_missing0000":
            raise UnknownPlanError(f"unknown plan {plan_id!r}; run prepare first")
        return models.StartResult(
            ok=True,
            state="running",
            plan_id=plan_id,
            experiment_id="exp-1",
            started=True,
        )

    def status(self, experiment_id):
        self.calls.append(("status", (experiment_id,), {}))
        return models.StatusResult(
            ok=True,
            experiment_id=experiment_id,
            state="RUNNING",
            final=False,
            tasks=["t1"],
            matrix={"base": {"t1": "P"}},
            totals={"base": {"P": 1, "F": 0, "E": 0}},
            report=None,
        )

    def cancel(self, experiment_id):
        self.calls.append(("cancel", (experiment_id,), {}))
        return models.CancelResult(
            ok=True, experiment_id=experiment_id, state="CANCELLING", cancelled=True
        )

    def report(self, experiment_id):
        self.calls.append(("report", (experiment_id,), {}))
        return models.ReportResult(
            ok=True,
            experiment_id=experiment_id,
            csv_path="/tmp/x.csv",
            markdown_path="/tmp/x.md",
        )


def _call(service: FakeService, arguments: dict) -> dict:
    return dispatch(service, "tools/call", {"name": TOOL_NAME, "arguments": arguments})


def _text(result: dict) -> Any:
    return json.loads(result["content"][0]["text"])


class TestInitialize:
    def test_known_version_echoed_with_capabilities(self):
        result = dispatch(FakeService(), "initialize", {"protocolVersion": "2025-03-26"})
        assert result["protocolVersion"] == "2025-03-26"
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == "roastmyharness"

    def test_unknown_version_falls_back(self):
        result = dispatch(FakeService(), "initialize", {"protocolVersion": "1999-01-01"})
        assert result["protocolVersion"] == "2024-11-05"

    def test_ping(self):
        assert dispatch(FakeService(), "ping", {}) == {}

    def test_unknown_method_rejected(self):
        with pytest.raises(Exception) as exc:
            dispatch(FakeService(), "resources/list", {})
        assert exc.value.code == -32601


class TestToolsList:
    def test_lists_roast_harness_with_schema(self):
        result = dispatch(FakeService(), "tools/list", {})
        tool = result["tools"][0]
        assert tool["name"] == TOOL_NAME
        schema = tool["inputSchema"]
        assert schema["required"] == ["action"]
        assert set(schema["properties"]) == {
            "action",
            "spec_path",
            "plan_id",
            "experiment_id",
            "skip_docker",
        }
        assert schema["properties"]["action"]["enum"] == [
            "prepare",
            "start",
            "status",
            "cancel",
            "report",
        ]


class TestToolsCall:
    def test_prepare_passes_path_and_flags(self, tmp_path: Path):
        service = FakeService()
        result = _call(
            service,
            {"action": "prepare", "spec_path": str(tmp_path), "skip_docker": True},
        )
        action, args, kwargs = service.calls[0]
        assert (action, kwargs) == ("prepare", {"skip_docker": True})
        assert args[0] == tmp_path
        assert result.get("isError") is not True
        payload = _text(result)
        assert payload["state"] == "ready_for_confirmation"
        assert payload["plan_id"] == "plan_abc123def456"

    def test_prepare_without_path_is_invalid_params(self):
        with pytest.raises(Exception) as exc:
            _call(FakeService(), {"action": "prepare"})
        assert exc.value.code == -32602

    def test_start_requires_plan_id(self):
        with pytest.raises(Exception) as exc:
            _call(FakeService(), {"action": "start"})
        assert "plan_id" in exc.value.message

    def test_status_cancel_report_require_experiment_id(self):
        service = FakeService()
        for action in ("status", "cancel", "report"):
            with pytest.raises(Exception) as exc:
                _call(service, {"action": action})
            assert exc.value.code == -32602
            result = _call(service, {"action": action, "experiment_id": "exp-9"})
            assert _text(result)["ok"] is True

    def test_unknown_action_is_invalid_params(self):
        with pytest.raises(Exception) as exc:
            _call(FakeService(), {"action": "explode"})
        assert exc.value.code == -32602

    def test_unknown_tool_rejected(self):
        with pytest.raises(Exception) as exc:
            dispatch(FakeService(), "tools/call", {"name": "other", "arguments": {}})
        assert "unknown tool" in exc.value.message

    def test_service_error_maps_to_is_error_result(self):
        result = _call(FakeService(), {"action": "start", "plan_id": "plan_missing0000"})
        assert result["isError"] is True
        payload = _text(result)
        assert payload["error"]["code"] == "unknown_plan"

    def test_non_service_exception_is_internal_error(self):
        class Exploding(FakeService):
            def status(self, experiment_id):
                raise RuntimeError("boom")

        response = handle_request(
            Exploding(),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": TOOL_NAME,
                        "arguments": {"action": "status", "experiment_id": "e"},
                    },
                }
            ),
        )
        assert response is not None
        assert response["error"]["code"] == -32603


class TestJsonRpcFraming:
    def test_request_round_trip(self):
        response = handle_request(
            FakeService(),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": TOOL_NAME, "arguments": {"action": "ping_like"}},
                }
            ),
        )
        assert response is not None
        assert response["id"] == 1
        assert response["error"]["code"] == -32602

    def test_notification_gets_no_response(self):
        response = handle_request(
            FakeService(), json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )
        assert response is None

    def test_blank_line_is_ignored(self):
        assert handle_request(FakeService(), "   ") is None

    def test_parse_error_gets_error_response(self):
        response = handle_request(FakeService(), "{not json")
        assert response is not None
        assert response["error"]["code"] == -32700

    def test_invalid_request_shape(self):
        response = handle_request(FakeService(), json.dumps({"jsonrpc": "2.0"}))
        assert response is not None
        assert response["error"]["code"] == -32600

    def test_error_responses_carry_request_id(self):
        response = handle_request(
            FakeService(),
            json.dumps({"jsonrpc": "2.0", "id": 42, "method": "no/such", "params": {}}),
        )
        assert response["id"] == 42
        assert response["error"]["code"] == -32601


class TestServiceErrorSubclasses:
    def test_service_error_has_stable_code(self):
        assert ServiceError("x").code == "error"
        assert UnknownPlanError("x").code == "unknown_plan"
