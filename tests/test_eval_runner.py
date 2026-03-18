from pathlib import Path

from core.pod.pod import init_default_pods
from core.router.router import Router
from core.storage.artifact_store import ArtifactStore
from core.storage.db import Database
from core.workspace.service import WorkspaceService
from core_runtime.eval_runner import run_replay_eval_pack, run_smoke_eval_pack


class LocalState:
    def __init__(self, tmp_path: Path) -> None:
        self.db = Database(tmp_path / "evals.db")
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
        self._counter = 0

    def new_request_id(self) -> str:
        self._counter += 1
        return f"req_eval_{self._counter}"

    def try_begin_workflow(self, *, workflow_id: str, request_id: str, user_input: str) -> bool:
        return True

    def end_workflow(self, *, workflow_id: str) -> None:
        return None


def test_run_smoke_eval_pack(tmp_path: Path) -> None:
    report = run_smoke_eval_pack(
        Path("evals/smoke/workflow_smoke.json"),
        state_factory=lambda: LocalState(tmp_path / "smoke"),
    )
    assert report["suite"] == "smoke"
    assert report["failed"] == 0
    assert report["passed"] >= 1


def test_run_replay_eval_pack(tmp_path: Path) -> None:
    report = run_replay_eval_pack(
        Path("evals/replay_pack/replay_cases.json"),
        state_factory=lambda: LocalState(tmp_path / "replay"),
    )
    assert report["suite"] == "replay"
    assert report["failed"] == 0
    assert report["passed"] >= 1
