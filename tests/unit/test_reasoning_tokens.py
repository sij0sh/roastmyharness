"""omp emits usage.reasoningTokens where pi emits usage.reasoning; both
must fold into reasoning_tokens metrics."""

from roast_my_harness.adapter.atif import _metrics_from_usage
from roast_my_harness.telemetry.parser import fold_event, new_event_metrics


def _turn_end(usage: dict) -> dict:
    return {"type": "turn_end", "message": {"usage": usage}}


def test_pi_style_reasoning_key_still_folds():
    m = new_event_metrics()
    fold_event(m, _turn_end({"input": 10, "cacheRead": 0, "cacheWrite": 5, "reasoning": 42}))
    assert m["reasoning_tokens"] == 42
    assert m["llm_calls"] == 1


def test_omp_style_reasoning_tokens_key_folds():
    m = new_event_metrics()
    fold_event(
        m, _turn_end({"input": 10, "cacheRead": 0, "cacheWrite": 5, "reasoningTokens": 78})
    )
    assert m["reasoning_tokens"] == 78
    assert m["llm_calls"] == 1


def test_missing_reasoning_keys_fold_zero():
    m = new_event_metrics()
    fold_event(m, _turn_end({"input": 10, "cacheRead": 0, "cacheWrite": 5}))
    assert m["reasoning_tokens"] == 0


def test_atif_metrics_map_reasoning_tokens_key():
    metrics = _metrics_from_usage({"input": 3, "output": 199, "reasoningTokens": 78})
    assert metrics is not None
    assert metrics.extra["reasoning_tokens"] == 78


def test_atif_metrics_map_reasoning_key():
    metrics = _metrics_from_usage({"input": 3, "output": 199, "reasoning": 42})
    assert metrics is not None
    assert metrics.extra["reasoning_tokens"] == 42
