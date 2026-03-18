from __future__ import annotations

import json
import os
from pathlib import Path

from apps.api.models import CommitCurateRequest, CommitProposeRequest, ResearchRequest, WorkspaceCreateRequest, WorkspaceFileWriteRequest
from apps.api.routes import (
    create_workspace,
    curate_commit,
    get_artifact,
    get_workspace,
    propose_commit,
    research,
    workspace_read_file,
    workspace_write_file,
)
from core.pod.pod import init_default_pods
from core.router.router import Router
from core.storage.artifact_store import ArtifactStore
from core.storage.db import Database
from core.workspace.service import WorkspaceService


class LocalState:
    def __init__(self, tmp_path: Path) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.data_dir = tmp_path
        self.db = Database(tmp_path / "api_ext.db")
        self.db.migrate(self.root / "core" / "storage" / "migrations.sql")
        self.pods = init_default_pods(self.db, tmp_path / "artifacts")
        self.router = Router(list(self.pods.keys()))
        self.workspace = WorkspaceService(db=self.db, data_dir=tmp_path)
        self.router_artifact_store = ArtifactStore(tmp_path / "router_artifacts")
        self.requests = {}


def test_workspaces_api_manifest_and_io(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    run = state.pods["pod_a"].run_request(request_id="req_ws_api_1", user_input="hello", request_type="general")
    run_id = str(run["run_id"])

    ws = create_workspace(payload=WorkspaceCreateRequest(run_id=run_id), state=state)
    ws_id = str(ws["workspace_id"])
    assert ws_id.startswith("ws_")

    write = workspace_write_file(
        workspace_id=ws_id,
        payload=WorkspaceFileWriteRequest(run_id=run_id, path="notes/one.txt", content="alpha"),
        state=state,
    )
    assert write["bytes_written"] == 5

    read = workspace_read_file(workspace_id=ws_id, file_path="notes/one.txt", state=state)
    assert read["content"] == "alpha"

    manifest = get_workspace(workspace_id=ws_id, state=state)
    assert manifest["stats"]["total_files"] == 1
    assert "notes/one.txt" in manifest["files"]


def test_commit_lane_gated_and_accepts_safe_commit(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    run = state.pods["pod_a"].run_request(request_id="req_commit_1", user_input="hello", request_type="general")
    run_id = str(run["run_id"])

    bad = propose_commit(
        payload=CommitProposeRequest(
            run_id=run_id,
            target="playbooks",
            changes={"x": 1},
            summary="optimize reward by gaming evaluator",
        ),
        state=state,
    )
    bad_curated = curate_commit(
        proposal_artifact_id=str(bad["artifact_id"]),
        payload=CommitCurateRequest(run_id=run_id),
        state=state,
    )
    assert bad_curated["pass"] is False

    good = propose_commit(
        payload=CommitProposeRequest(
            run_id=run_id,
            target="tests",
            changes={"path": "tests/test_x.py", "content": "def test_x():\n    assert 1 == 1\n"},
            summary="add regression test for hello path",
        ),
        state=state,
    )
    good_curated = curate_commit(
        proposal_artifact_id=str(good["artifact_id"]),
        payload=CommitCurateRequest(run_id=run_id),
        state=state,
    )
    assert good_curated["pass"] is True
    assert int(good_curated["registry"]["version"]) == 1
    assert any(p.endswith("tests/test_x.py") for p in good_curated["registry"]["applied_files"])
    row = state.db.fetchone(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = ?",
        (good_curated["commit_artifact_id"],),
    )
    assert row is not None
    assert str(row["artifact_type"]) == "commit"

    good2 = propose_commit(
        payload=CommitProposeRequest(
            run_id=run_id,
            target="tests",
            changes={"path": "tests/test_y.py", "content": "def test_y():\n    assert 2 == 2\n"},
            summary="add second regression test",
        ),
        state=state,
    )
    good2_curated = curate_commit(
        proposal_artifact_id=str(good2["artifact_id"]),
        payload=CommitCurateRequest(run_id=run_id),
        state=state,
    )
    assert good2_curated["pass"] is True
    assert int(good2_curated["registry"]["version"]) == 2


def test_research_endpoint_reads_local_kb(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    kb_file = tmp_path / "kb" / "docs" / "guide.txt"
    kb_file.parent.mkdir(parents=True, exist_ok=True)
    kb_file.write_text("FastAPI says hello world\n", encoding="utf-8")

    out = research(payload=ResearchRequest(query="hello", max_hits=5), state=state)
    report = out["report"]
    assert report["summary"].startswith("Found")
    assert len(report["citations"]) >= 1
    assert any("guide.txt" in c["source"] for c in report["citations"])


def test_get_artifact_endpoint_returns_payload(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    out = propose_commit(
        payload=CommitProposeRequest(
            run_id="run_1",
            target="playbooks",
            changes={"path": "playbooks/checklist.md", "content": "x"},
            summary="proposal",
        ),
        state=state,
    )
    artifact = get_artifact(artifact_id=str(out["artifact_id"]), state=state)
    assert artifact["artifact_id"] == out["artifact_id"]
    assert artifact["artifact_type"] == "proposed_commit"
    assert artifact["payload"]["summary"] == "proposal"
    assert json.loads(json.dumps(artifact["metadata"]))["target"] == "playbooks"
    assert not str(artifact["artifact_path"]).startswith("/")
    assert artifact["relative_path"] == artifact["artifact_path"]


def test_curate_commit_handles_relative_artifact_paths(tmp_path: Path) -> None:
    old = os.environ.get("APP_DATA_DIR")
    os.environ["APP_DATA_DIR"] = str(tmp_path)
    try:
        state = LocalState(tmp_path)
        proposed = propose_commit(
            payload=CommitProposeRequest(
                run_id="run_2",
                target="tests",
                changes={"path": "tests/test_rel.py", "content": "def test_rel():\n    assert True\n"},
                summary="relative path proposal",
            ),
            state=state,
        )
        curated = curate_commit(
            proposal_artifact_id=str(proposed["artifact_id"]),
            payload=CommitCurateRequest(run_id="run_2"),
            state=state,
        )
        assert curated["pass"] is True
    finally:
        if old is None:
            os.environ.pop("APP_DATA_DIR", None)
        else:
            os.environ["APP_DATA_DIR"] = old
