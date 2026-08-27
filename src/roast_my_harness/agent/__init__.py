"""Machine-facing orchestration: one service, thin clients on top.

The agent service exposes prepare/start/status/cancel/report with stable
JSON shapes so the Typer CLI, the Pi extension, and the MCP server stay
behaviorally identical. Plans are persisted under data_dir()/plans and
bound to the exact bytes they approved.
"""
