from __future__ import annotations


def compute_signal_score(*, completion: float, retries: float, return_use: float, latency_ms: float) -> float:
    latency_component = max(0.0, 1.0 - (latency_ms / 10_000.0))
    return (0.5 * completion) + (0.3 * return_use) + (0.2 * latency_component) - (0.3 * retries)
