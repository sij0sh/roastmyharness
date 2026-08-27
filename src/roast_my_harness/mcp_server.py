"""Minimal stdio MCP server exposing the roast_harness tool.

Speaks the Model Context Protocol over newline-delimited JSON-RPC on
stdin/stdout using only the standard library, and imports the same
AgentService the Pi extension and CLI use, so validation, plan binding,
idempotent start, and result shapes stay identical across clients.

Run with ``python -m roast_my_harness.mcp_server``.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

from roast_my_harness import __version__
from roast_my_harness.agent.service import AgentService, ServiceError

TOOL_NAME = "roast_harness"
TOOL_DESCRIPTION = (
    "Run harness-comparison experiments via the roastmyharness service. "
    "prepare validates an experiment TOML or YAML file and returns a plan for user "
    "approval; start launches an approved plan_id in the background; status "
    "polls an experiment; cancel requests graceful cancellation; report "
    "regenerates artifacts."
)
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")

TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["prepare", "start", "status", "cancel", "report"],
            "description": "Orchestration action to perform.",
        },
        "spec_path": {
            "type": "string",
            "description": "Experiment TOML or YAML path (required for prepare).",
        },
        "plan_id": {
            "type": "string",
            "description": "Plan id from prepare (required for start).",
        },
        "experiment_id": {
            "type": "string",
            "description": "Experiment id (required for status, cancel, report).",
        },
        "skip_docker": {
            "type": "boolean",
            "description": "Skip docker preflight checks.",
        },
    },
    "required": ["action"],
}


class McpError(Exception):
    """JSON-RPC protocol error."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _invalid_params(message: str) -> McpError:
    return McpError(-32602, message)


def dispatch(service: AgentService, method: str, params: Any) -> Any:
    """Handle one non-notification request; raise McpError on protocol errors."""
    if method == "initialize":
        return {
            "protocolVersion": _negotiated_version(params),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "roastmyharness", "version": __version__},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": TOOL_DESCRIPTION,
                    "inputSchema": TOOL_INPUT_SCHEMA,
                }
            ]
        }
    if method == "tools/call":
        if not isinstance(params, dict):
            raise _invalid_params("tools/call params must be an object")
        name = params.get("name")
        if name != TOOL_NAME:
            raise McpError(-32602, f"unknown tool {name!r}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise _invalid_params("arguments must be an object")
        return _call_tool(service, arguments)
    raise McpError(-32601, f"method not found: {method}")


def _negotiated_version(params: Any) -> str:
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return str(requested)
    return SUPPORTED_PROTOCOL_VERSIONS[0]


def _call_tool(service: AgentService, args: dict[str, Any]) -> dict[str, Any]:
    action = args.get("action")
    skip_docker = bool(args.get("skip_docker", False))
    try:
        if action == "prepare":
            spec_path = args.get("spec_path")
            if not spec_path:
                raise _invalid_params("spec_path is required for prepare")
            result = service.prepare(_expand(spec_path), skip_docker=skip_docker)
        elif action == "start":
            plan_id = args.get("plan_id")
            if not plan_id:
                raise _invalid_params("plan_id is required for start")
            result = service.start(str(plan_id), skip_docker=skip_docker)
        elif action in ("status", "cancel", "report"):
            experiment_id = args.get("experiment_id")
            if not experiment_id:
                raise _invalid_params(f"experiment_id is required for {action}")
            method = getattr(service, action)
            result = method(str(experiment_id))
        else:
            raise _invalid_params(f"unknown action {action!r}")
    except McpError:
        raise
    except ServiceError as error:
        payload = {"ok": False, "error": {"code": error.code, "message": str(error)}}
        return {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
            "isError": True,
        }
    return {
        "content": [
            {
                "type": "text",
                "text": result.model_dump_json(exclude_none=True, indent=2),
            }
        ]
    }


def _expand(path: str) -> Any:
    from pathlib import Path

    return Path(str(path)).expanduser()


def handle_request(service: AgentService, line: str) -> dict[str, Any] | None:
    """Process one input line; return a response object or None for notices."""
    line = line.strip()
    if not line:
        return None
    try:
        request = json.loads(line)
    except json.JSONDecodeError as error:
        return _error(None, McpError(-32700, f"parse error: {error}"))
    if not isinstance(request, dict) or "method" not in request:
        return _error(
            request.get("id") if isinstance(request, dict) else None,
            McpError(-32600, "invalid request"),
        )
    request_id = request.get("id")
    is_notification = request_id is None
    try:
        result = dispatch(service, str(request["method"]), request.get("params"))
    except McpError as error:
        return None if is_notification else _error(request_id, error)
    except Exception as error:
        return (
            None
            if is_notification
            else _error(request_id, McpError(-32603, f"internal error: {error}"))
        )
    return None if is_notification else {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, error: McpError) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": error.code, "message": error.message},
    }


def serve(service: AgentService, stdin: Any, stdout: Any) -> None:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    for line in stdin:
        response = handle_request(service, line)
        if response is not None:
            data = json.dumps(response) + "\n"
            stdout.write(data if isinstance(stdout, io.TextIOBase) else data.encode())
            stdout.flush()


def main() -> int:
    serve(AgentService(), sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
