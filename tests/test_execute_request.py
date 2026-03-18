from pathlib import Path

from core_runtime.execute import execute_request
from core.router.router import Router
from core.pod.pod import init_default_pods
from core.storage.artifact_store import ArtifactStore
from core.storage.db import Database
from core.workspace.service import WorkspaceService


class LocalState:
    def __init__(self, tmp_path: Path) -> None:
        self.db = Database(tmp_path / "runtime.db")
        repo_root = Path(__file__).resolve().parents[1]
        self.root = repo_root
        self.config_dir = repo_root / "config"
        self.data_dir = tmp_path
        self.db.migrate(repo_root / "core" / "storage" / "migrations.sql")
        self.pods = init_default_pods(self.db, tmp_path / "artifacts", config_dir=self.config_dir)
        self.router = Router(list(self.pods.keys()))
        self.workspace = WorkspaceService(db=self.db, data_dir=tmp_path)
        self.router_artifact_store = ArtifactStore(tmp_path / "router_artifacts")
        self.requests = {}

    def new_request_id(self) -> str:
        return "req_runtime_1"

    def try_begin_workflow(self, *, workflow_id: str, request_id: str, user_input: str) -> bool:
        return True

    def end_workflow(self, *, workflow_id: str) -> None:
        return None


def test_execute_request_runs_shared_workflow_runtime(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    out = execute_request(
        {
            "user_input": "build a weather station app",
            "request_type": "coding",
            "max_steps": 4,
            "retry_same_persona_once": False,
        },
        state=state,
    )
    assert out["workflow_id"].startswith("wf_")
    assert out["request_id"].startswith("req_")
    assert out["canonical_target"] == "weather_station_app"
    assert "steps" in out
    assert not str(out["planner_artifact_path"]).startswith("/")
    assert not str(out["decomposition_plan_artifact_path"]).startswith("/")
    assert "planner_artifact_id" in out
    assert "workflow_graph_artifact_id" in out
    assert "workflow_graph_relative_path" in out
    assert "spawn" in out
    assert "clarification" not in out


def test_execute_request_contract_is_whitelisted(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    out = execute_request(
        {
            "user_input": "hello pod",
            "request_type": "general",
            "max_steps": 2,
            "retry_same_persona_once": False,
        },
        state=state,
    )
    allowed_keys = {
        "workflow_id",
        "request_id",
        "pod_id",
        "steps",
        "workflow_graph_artifact_id",
        "workflow_graph_artifact_path",
        "workflow_graph_relative_path",
        "planner_artifact_id",
        "planner_artifact_path",
        "planner_relative_path",
        "planner",
        "decomposition_plan_artifact_id",
        "decomposition_plan_artifact_path",
        "decomposition_plan_relative_path",
        "decomposition_plan",
        "canonical_target",
        "workspace_id",
        "workspace_manifest",
        "service",
        "service_url",
        "service_hello_url",
        "auto_commit",
        "learning",
        "final_winner_run_id",
        "final_status",
        "final_pass",
        "stop_reason",
        "spawn",
        "clarification",
    }
    assert set(out.keys()).issubset(allowed_keys)
