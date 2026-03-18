from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlparse

from core.pod.pod import init_default_pods
from core.router.resources import ResourceAllocator
from core.router.router import Router
from core.storage.artifact_store import ArtifactStore
from core.storage.db import Database
from core.workspace.service import WorkspaceService


class AppState:
    def __init__(self) -> None:
        root = Path(__file__).resolve().parents[2]
        data_dir = Path(os.getenv("APP_DATA_DIR", str(root / "data"))).resolve()
        config_dir = Path(os.getenv("APP_CONFIG_DIR", str(root / "config"))).resolve()
        os.environ.setdefault("APP_DATA_DIR", str(data_dir))
        os.environ.setdefault("APP_CONFIG_DIR", str(config_dir))
        db_url = os.getenv("DATABASE_URL", "")
        db_path = data_dir / "agent_pods.db"
        if db_url.startswith("sqlite:///"):
            parsed = urlparse(db_url)
            if parsed.path:
                db_path = Path(parsed.path).resolve()
        self.root = root
        self.config_dir = config_dir
        self.data_dir = data_dir
        self.db = Database(db_path)
        self.db.migrate(root / "core" / "storage" / "migrations.sql")
        artifact_root = Path(os.getenv("ARTIFACT_DIR", str(data_dir / "artifacts"))).resolve()
        self.pods = init_default_pods(self.db, artifact_root, config_dir=self.config_dir)
        self.router = Router(list(self.pods.keys()))
        self.workspace = WorkspaceService(db=self.db, data_dir=data_dir)
        self.router_artifact_store = ArtifactStore(data_dir / "router_artifacts")
        persisted_weights = self.db.load_routing_weights()
        for pod_id, weight in persisted_weights.items():
            if pod_id in self.router.weights:
                self.router.update_weight(pod_id, weight)
        for request_type in ["general", "web_service", "coding", "research"]:
            typed = self.db.load_routing_weights_by_type(request_type=request_type)
            for pod_id, weight in typed.items():
                if pod_id in self.router.weights:
                    self.router.update_weight_for_type(request_type, pod_id, weight)
        self.allocator = ResourceAllocator(db=self.db, router=self.router)
        self.routing_mode = os.getenv("ROUTING_MODE", "auto").lower()  # auto|broadcast|weighted
        self.requests: dict[str, dict[str, Any]] = {}
        self._workflow_lock = threading.Lock()
        self._active_workflow: dict[str, Any] | None = None

    def new_request_id(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"req_{ts}"

    def try_begin_workflow(self, *, workflow_id: str, request_id: str, user_input: str) -> bool:
        stale_timeout_s = int(os.getenv("WORKFLOW_LOCK_STALE_SECONDS", "1800"))
        if self._workflow_lock.locked() and self._active_workflow:
            started_at = str(self._active_workflow.get("started_at") or "")
            try:
                started_dt = datetime.fromisoformat(started_at)
            except ValueError:
                started_dt = None
            if started_dt is not None:
                age_s = int((datetime.now(timezone.utc) - started_dt).total_seconds())
                if age_s > stale_timeout_s:
                    self._active_workflow = None
                    try:
                        self._workflow_lock.release()
                    except RuntimeError:
                        pass
        acquired = self._workflow_lock.acquire(blocking=False)
        if not acquired:
            return False
        self._active_workflow = {
            "workflow_id": workflow_id,
            "request_id": request_id,
            "user_input": user_input,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        return True

    def end_workflow(self, *, workflow_id: str) -> None:
        active = self._active_workflow or {}
        if str(active.get("workflow_id") or "") == workflow_id:
            self._active_workflow = None
        if self._workflow_lock.locked():
            self._workflow_lock.release()

    def get_active_workflow(self) -> dict[str, Any] | None:
        if not self._active_workflow:
            return None
        return dict(self._active_workflow)


STATE = AppState()


def get_state() -> AppState:
    return STATE
