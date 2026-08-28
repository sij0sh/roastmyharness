"""Pi JSON-mode events -> Pier ATIF trajectory. Golden-compatible port.

Imports the real pier model classes so serialized output is identical by
construction (pier is a dev/test dependency; at runtime this module is
only imported inside pier's own process).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pier.models.trajectories import (
    Agent,
    FinalMetrics,
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)


def _iso_from_ms(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000.0, tz=UTC).isoformat()


def content_text(content: Any) -> str:
    """Join text parts of a pi message content array."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def _tool_calls_from_message(
    message: dict[str, Any], step_id: int
) -> list[ToolCall] | None:
    calls: list[ToolCall] = []
    for block in message.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "toolCall":
            calls.append(
                ToolCall(
                    tool_call_id=str(
                        block.get("id") or f"call_{step_id}_{len(calls) + 1}"
                    ),
                    function_name=str(block.get("name") or "unknown"),
                    arguments=block.get("arguments") or {},
                )
            )
    return calls or None


def _reasoning_from_message(message: dict[str, Any]) -> str | None:
    parts = [
        str(block.get("thinking", ""))
        for block in message.get("content") or []
        if isinstance(block, dict)
        and block.get("type") == "thinking"
        and block.get("thinking")
    ]
    return "\n".join(parts) or None


def _metrics_from_usage(usage: dict[str, Any]) -> Metrics | None:
    prompt = int(usage.get("input") or 0)
    completion = int(usage.get("output") or 0)
    if prompt == 0 and completion == 0:
        return None
    cost = (usage.get("cost") or {}).get("total") or 0
    extra: dict[str, Any] = {}
    if usage.get("reasoning") is not None:
        extra["reasoning_tokens"] = usage["reasoning"]
    elif usage.get("reasoningTokens") is not None:
        # omp (pi fork) names the key reasoningTokens
        extra["reasoning_tokens"] = usage["reasoningTokens"]
    if usage.get("cacheWrite"):
        extra["cache_write_tokens"] = usage["cacheWrite"]
    return Metrics(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=int(usage.get("cacheRead") or 0) or None,
        cost_usd=float(cost) if cost else None,
        extra=extra or None,
    )


def convert_pi_events_to_atif(
    events: list[dict[str, Any]],
    *,
    instruction: str,
    variant: str,
    agent_version: str,
) -> Trajectory:
    """Convert pi JSON-mode events to an ATIF trajectory.

    Reads turn_end events (authoritative per-turn records that survive any
    context compaction), the session header, and compaction_* counters.
    """
    session_id = "unknown"
    model_name: str | None = None
    steps: list[Step] = []
    step_id = 1
    compactions = 0

    if instruction:
        steps.append(Step(step_id=step_id, source="user", message=instruction))
        step_id += 1

    total_prompt = total_completion = total_cached = 0
    total_cost = 0.0
    peak_context: int | None = None

    for event in events:
        etype = event.get("type")

        if etype == "session":
            session_id = str(event.get("id") or session_id)
        elif etype == "compaction_end":
            compactions += 1
        elif etype == "turn_end":
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                continue
            usage = message.get("usage") or {}
            metrics = _metrics_from_usage(usage)
            model_name = message.get("model") or model_name

            prompt = int(usage.get("input") or 0)
            peak_context = prompt if peak_context is None else max(peak_context, prompt)
            total_prompt += prompt
            total_completion += int(usage.get("output") or 0)
            total_cached += int(usage.get("cacheRead") or 0)
            total_cost += float((usage.get("cost") or {}).get("total") or 0)

            steps.append(
                Step(
                    step_id=step_id,
                    timestamp=_iso_from_ms(message.get("timestamp")),
                    source="agent",
                    model_name=message.get("model"),
                    message=content_text(message.get("content")),
                    reasoning_content=_reasoning_from_message(message),
                    tool_calls=_tool_calls_from_message(message, step_id),
                    metrics=metrics,
                )
            )
            step_id += 1

            results = [
                ObservationResult(
                    content=content_text(result.get("content")),
                    extra=(
                        {
                            "tool": result.get("toolName"),
                            "is_error": bool(result.get("isError")),
                        }
                        if result.get("toolName")
                        else None
                    ),
                )
                for result in event.get("toolResults") or []
            ]
            if results:
                steps[-1].observation = Observation(results=results)

    if not steps:
        steps.append(Step(step_id=1, source="system", message="No agent turns recorded."))

    extra = {
        "peak_context_tokens": peak_context,
        "summarization_count": compactions,
        "pi_variant": variant,
    }
    final_metrics = FinalMetrics(
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        total_cached_tokens=total_cached or None,
        total_cost_usd=round(total_cost, 6),
        total_steps=max(len(steps) - 1, 0),
        extra=extra,
    )
    return Trajectory(
        agent=Agent(
            name="pi",
            version=agent_version,
            model_name=model_name,
            extra={"variant": variant},
        ),
        session_id=session_id,
        steps=steps,
        final_metrics=final_metrics,
        notes=(
            f"pi coding agent, variant={variant}; converted from pi JSON-mode "
            "event stream (turn_end events)."
        ),
    )


def write_trajectory(events_path, out_path, *, instruction, variant, agent_version):
    events = [
        json.loads(line)
        for line in events_path.read_text().splitlines()
        if line.strip()
    ]
    trajectory = convert_pi_events_to_atif(
        events,
        instruction=instruction,
        variant=variant,
        agent_version=agent_version,
    )
    out_path.write_text(json.dumps(trajectory.to_json_dict(), indent=2))
    return trajectory
