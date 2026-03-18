from __future__ import annotations

import json
from typing import Dict, List

from core.pod.lineage import utc_now_iso
from core.storage.db import Database
from core.router.router import Router


class ResourceAllocator:
    def __init__(self, *, db: Database, router: Router) -> None:
        self.db = db
        self.router = router
        self.incubation_default = 5

    def _base_weights(self, request_type: str, pod_ids: List[str]) -> Dict[str, float]:
        typed = self.router.weights_by_type.get(request_type, {})
        return {p: float(typed.get(p, self.router.weights.get(p, 1.0))) for p in pod_ids}

    def refresh(self, *, request_type: str, pod_ids: List[str]) -> None:
        weights = self._base_weights(request_type, pod_ids)
        total = sum(max(0.01, w) for w in weights.values()) or 1.0

        for pod_id in pod_ids:
            row = self.db.get_pod_resource_state(request_type=request_type, pod_id=pod_id)
            cfg_row = self.db.fetchone("SELECT config_json FROM pods WHERE pod_id = ?", (pod_id,))
            cfg = json.loads(str(cfg_row["config_json"])) if cfg_row else {}

            normalized = max(0.01, weights.get(pod_id, 1.0)) / total
            compute_budget = round(max(0.1, min(5.0, normalized * 10.0)), 3)
            traffic_cap = max(0.05, min(0.8, normalized + 0.1))

            if row is None:
                is_generated = pod_id.startswith("pod_gen_")
                incubation_budget = self.incubation_default if is_generated else 0
                assigned_requests = 0
            else:
                incubation_budget = int(row["incubation_budget"])
                assigned_requests = int(row["assigned_requests"])

            completions = self.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="completion")
            retries = self.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="retry")
            abandons = self.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="abandon")
            attempts = completions + retries + abandons
            completion_rate = completions / max(1, attempts)
            is_starved = attempts >= 5 and completion_rate < 0.2 and incubation_budget <= 0

            self.db.upsert_pod_resource_state(
                request_type=request_type,
                pod_id=pod_id,
                updated_at=utc_now_iso(),
                compute_budget=compute_budget,
                traffic_cap=traffic_cap,
                incubation_budget=incubation_budget,
                is_starved=is_starved,
                assigned_requests=assigned_requests,
                metadata_json=json.dumps(
                    {
                        "normalized_weight": normalized,
                        "completion_rate": completion_rate,
                        "attempts": attempts,
                        "routing_strategy": cfg.get("routing_strategy", "weighted"),
                    }
                ),
            )

    def eligible_pods(self, *, request_type: str, pod_ids: List[str]) -> List[str]:
        self.refresh(request_type=request_type, pod_ids=pod_ids)
        states = {str(r["pod_id"]): r for r in self.db.list_pod_resource_state(request_type=request_type)}
        total_assigned = sum(int(r["assigned_requests"]) for r in states.values())

        allowed: List[str] = []
        for pod_id in pod_ids:
            row = states.get(pod_id)
            if row is None:
                allowed.append(pod_id)
                continue
            incubation_budget = int(row["incubation_budget"])
            is_starved = bool(row["is_starved"])
            if is_starved and incubation_budget <= 0:
                continue

            assigned = int(row["assigned_requests"])
            ratio = assigned / max(1, total_assigned)
            cap = float(row["traffic_cap"])
            if ratio <= cap or assigned == 0 or incubation_budget > 0:
                allowed.append(pod_id)

        if not allowed:
            # Fallback: keep at least one pod alive for exploration.
            return pod_ids[:1]
        return allowed

    def mark_dispatch(self, *, request_type: str, pod_id: str) -> None:
        row = self.db.get_pod_resource_state(request_type=request_type, pod_id=pod_id)
        if row is None:
            return
        incubation_budget = max(0, int(row["incubation_budget"]) - 1)
        assigned = int(row["assigned_requests"]) + 1
        self.db.upsert_pod_resource_state(
            request_type=request_type,
            pod_id=pod_id,
            updated_at=utc_now_iso(),
            compute_budget=float(row["compute_budget"]),
            traffic_cap=float(row["traffic_cap"]),
            incubation_budget=incubation_budget,
            is_starved=bool(row["is_starved"]),
            assigned_requests=assigned,
            metadata_json=row["metadata_json"],
        )
