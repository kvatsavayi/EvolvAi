from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from core.router.bandit import choose_weighted
from core.router.signals import compute_signal_score


@dataclass
class RoutingDecision:
    pod_ids: list[str]
    mode: str


class Router:
    def __init__(self, pod_ids: list[str]) -> None:
        self.pod_ids = pod_ids
        self.weights = {pod: 1.0 for pod in pod_ids}
        self.weights_by_type: dict[str, dict[str, float]] = {}
        self.learning_rate = 0.2
        self.min_weight = 0.1
        self.max_weight = 10.0

    def route(self, request_type: str = "general", mode: str = "broadcast", pod_ids: Optional[List[str]] = None) -> RoutingDecision:
        candidates = list(pod_ids) if pod_ids is not None else list(self.pod_ids)
        if mode == "broadcast":
            return RoutingDecision(pod_ids=candidates, mode=mode)
        if mode == "weighted":
            per_type = self.weights_by_type.get(request_type, {})
            weighted = {k: per_type.get(k, self.weights.get(k, 1.0)) for k in candidates}
            pod = choose_weighted(weighted or self.weights)
            return RoutingDecision(pod_ids=[pod], mode=mode)
        specialized = [pod for pod in candidates if request_type in pod]
        return RoutingDecision(pod_ids=specialized or candidates, mode="specialized")

    def update_weight(self, pod_id: str, value: float) -> None:
        clamped = max(self.min_weight, min(self.max_weight, value))
        self.weights[pod_id] = clamped

    def update_weight_for_type(self, request_type: str, pod_id: str, value: float) -> None:
        clamped = max(self.min_weight, min(self.max_weight, value))
        if request_type not in self.weights_by_type:
            self.weights_by_type[request_type] = {}
        self.weights_by_type[request_type][pod_id] = clamped

    def apply_signal_pressure(
        self,
        *,
        pod_id: str,
        request_type: str,
        completion_rate: float,
        retry_rate: float,
        return_rate: float,
        time_to_resolution_ms: float,
    ) -> float:
        reward = compute_signal_score(
            completion=completion_rate,
            retries=retry_rate,
            return_use=return_rate,
            latency_ms=time_to_resolution_ms,
        )
        per_type = self.weights_by_type.get(request_type, {})
        current = per_type.get(pod_id, self.weights.get(pod_id, 1.0))
        updated = current + (self.learning_rate * reward)
        self.update_weight_for_type(request_type, pod_id, updated)
        return self.weights_by_type[request_type][pod_id]
