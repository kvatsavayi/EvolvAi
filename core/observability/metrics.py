from __future__ import annotations

from collections import defaultdict


class Metrics:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, float]] = defaultdict(dict)

    def set(self, pod_id: str, metric: str, value: float) -> None:
        self._store[pod_id][metric] = value

    def get_for_pod(self, pod_id: str) -> dict[str, float]:
        return self._store.get(pod_id, {})
