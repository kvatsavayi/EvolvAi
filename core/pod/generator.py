from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.observability.canonical import canonical_sha256
from core.pod.lineage import make_lineage_edge, utc_now_iso
from core.router.signals import compute_signal_score
from core.storage.artifact_store import ArtifactStore
from core.storage.db import Database


@dataclass
class PodVariant:
    pod_id: str
    parent_pod_id: str
    config: Dict[str, Any]


class PodGenerator:
    def __init__(self, *, db: Database, artifact_root: Path) -> None:
        self.db = db
        self.store = ArtifactStore(artifact_root / "pod_generator")

    def external_score(self, pod_id: str, request_type: Optional[str] = None) -> float:
        completions = self.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="completion")
        retries = self.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="retry")
        returns = self.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="return")
        abandons = self.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="abandon")
        latency = self.db.avg_signal(pod_id=pod_id, request_type=request_type, signal_type="latency")

        attempts = max(1, completions + retries + abandons)
        completion_rate = completions / attempts
        retry_rate = retries / attempts
        return_rate = returns / max(1, completions)
        latency_ms = latency if latency > 0 else 10_000.0
        return compute_signal_score(
            completion=completion_rate,
            retries=retry_rate,
            return_use=return_rate,
            latency_ms=latency_ms,
        )

    def select_parent_pod(self, *, request_type: Optional[str] = None) -> str:
        rows = self.db.fetchall("SELECT pod_id FROM pods WHERE is_enabled = 1 ORDER BY pod_id")
        if not rows:
            raise ValueError("no enabled pods")
        scored = []
        for r in rows:
            pod_id = str(r["pod_id"])
            scored.append((self.external_score(pod_id, request_type=request_type), pod_id))
        scored.sort(reverse=True)
        return scored[0][1]

    def _base_config(self, parent_pod_id: str) -> Dict[str, Any]:
        row = self.db.fetchone("SELECT config_json FROM pods WHERE pod_id = ?", (parent_pod_id,))
        if row is None:
            raise ValueError(f"parent pod not found: {parent_pod_id}")
        return json.loads(str(row["config_json"]))

    def _make_variant_configs(self, parent_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        budgets = dict(parent_cfg.get("tool_budgets") or {"max_total_tool_calls": 5, "max_http_get": 3})
        max_total = int(budgets.get("max_total_tool_calls", 5))
        max_http = int(budgets.get("max_http_get", 3))

        v1 = dict(parent_cfg)
        v1["judge_rubric"] = "web_service" if parent_cfg.get("judge_rubric") != "web_service" else "base"

        v2 = dict(parent_cfg)
        v2["tool_budgets"] = {
            "max_total_tool_calls": max(1, min(8, max_total - 1)),
            "max_http_get": max(1, min(6, max_http - 1)),
        }

        v3 = dict(parent_cfg)
        current_route = str(parent_cfg.get("routing_strategy", "broadcast"))
        v3["routing_strategy"] = "weighted" if current_route != "weighted" else "specialized"

        v4 = dict(parent_cfg)
        current_mut = str(parent_cfg.get("mutation_operator", "verbosity_toggle"))
        v4["mutation_operator"] = "constraint_reorder" if current_mut != "constraint_reorder" else "verbosity_toggle"

        return [v1, v2, v3, v4]

    def generate(self, *, count: int = 1, request_type: Optional[str] = None) -> List[PodVariant]:
        parent_pod_id = self.select_parent_pod(request_type=request_type)
        parent_cfg = self._base_config(parent_pod_id)
        variants = self._make_variant_configs(parent_cfg)

        created: List[PodVariant] = []
        for cfg in variants[: max(1, count)]:
            cfg = dict(cfg)
            cfg["parent_pod_id"] = parent_pod_id
            cfg["generated_at"] = utc_now_iso()
            cfg_hash = canonical_sha256(cfg)
            pod_id = f"pod_gen_{cfg_hash[:8]}"
            cfg["pod_id"] = pod_id

            self.db.insert_pod(
                pod_id=pod_id,
                created_at=utc_now_iso(),
                is_enabled=True,
                config_json=json.dumps(cfg),
            )
            self.db.upsert_routing_weight(
                pod_id=pod_id,
                updated_at=utc_now_iso(),
                weight=0.5,
                metadata_json=json.dumps({"generated": True, "parent_pod_id": parent_pod_id}),
            )
            for rt in ["general", "web_service", "coding", "research"]:
                self.db.upsert_routing_weight_by_type(
                    request_type=rt,
                    pod_id=pod_id,
                    updated_at=utc_now_iso(),
                    weight=0.5,
                    metadata_json=json.dumps({"generated": True, "parent_pod_id": parent_pod_id}),
                )

            artifact_id, artifact_path = self.store.put_json(cfg)
            self.db.insert_artifact_registry(
                artifact_id=artifact_id,
                created_at=utc_now_iso(),
                artifact_type="pod_config",
                content_hash=canonical_sha256(cfg),
                artifact_path=artifact_path,
                metadata_json=json.dumps({"pod_id": pod_id, "parent_pod_id": parent_pod_id}),
            )
            edge = make_lineage_edge(
                parent_type="artifact",
                parent_id=f"pod:{parent_pod_id}",
                child_type="artifact",
                child_id=f"pod:{pod_id}",
                reason="mutation",
                run_id=None,
            )
            self.db.insert_lineage_edge(
                edge_id=edge.edge_id,
                parent_type=edge.parent_type,
                parent_id=edge.parent_id,
                child_type=edge.child_type,
                child_id=edge.child_id,
                reason=edge.reason,
                run_id=None,
                created_at=edge.created_at,
                metadata_json=json.dumps({"artifact_id": artifact_id}),
            )
            created.append(PodVariant(pod_id=pod_id, parent_pod_id=parent_pod_id, config=cfg))
        return created
