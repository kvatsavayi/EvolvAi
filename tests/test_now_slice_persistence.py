import json
from pathlib import Path

from core.pod.pod import init_default_pods
from core.storage.db import Database


def test_now_slice_is_persisted_and_linked_to_run(tmp_path: Path) -> None:
    db = Database(tmp_path / "now_slice.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    result = pods["pod_a"].run_request(
        request_id="req_now_slice_1",
        user_input="hello",
        request_type="general",
    )
    run_id = str(result["run_id"])
    now_slice_id = str(result["now_slice_id"])

    run_row = db.fetchone("SELECT now_slice_id FROM runs WHERE run_id = ?", (run_id,))
    assert run_row is not None
    assert str(run_row["now_slice_id"]) == now_slice_id

    now_row = db.fetchone(
        "SELECT now_slice_id, snapshot_id, now_band, execution_permission, artifact_path, persona_id, persona_version, handoff_artifact_id FROM now_slices WHERE now_slice_id = ?",
        (now_slice_id,),
    )
    assert now_row is not None
    assert str(now_row["now_slice_id"]) == now_slice_id
    assert str(now_row["snapshot_id"]) == str(result["snapshot_id"])
    assert str(now_row["now_band"]) in {"micro", "local", "sleep"}
    assert int(now_row["execution_permission"]) == 1
    assert str(now_row["persona_id"]) in {"general", "reviewer", "planner", "dev", "tester", "release", "maintainer"}
    assert str(now_row["persona_version"]).startswith("pv_")
    assert now_row["handoff_artifact_id"] is None
    assert str(now_row["artifact_path"]).endswith(".json")
    now_path = Path(str(now_row["artifact_path"]))
    if not now_path.is_absolute():
        now_path = tmp_path / now_path
    now_payload = json.loads(now_path.read_text(encoding="utf-8"))
    model_rail = now_payload.get("model_rail") or {}
    assert str(model_rail.get("provider")) != ""
    assert str(model_rail.get("model")) != ""
    assert "inference_params" in model_rail
    prompt_contract = now_payload.get("prompt_contract") or {}
    assert str(prompt_contract.get("executor_prompt_template_id", "")).startswith("ept_")
    assert str(prompt_contract.get("executor_prompt_hash", "")).startswith("h_")

    attempt_row = db.fetchone(
        "SELECT persona_id, persona_version, handoff_artifact_id, inference_params_json FROM run_attempts WHERE run_id = ? ORDER BY attempt_num ASC LIMIT 1",
        (run_id,),
    )
    assert attempt_row is not None
    assert str(attempt_row["persona_id"]) == str(now_row["persona_id"])
    assert str(attempt_row["persona_version"]) == str(now_row["persona_version"])
    assert attempt_row["handoff_artifact_id"] is None
    inference = json.loads(str(attempt_row["inference_params_json"] or "{}"))
    assert str(inference.get("provider", "")) != ""
    assert str(inference.get("model", "")) != ""
    assert "model_digest" in inference
    assert "seed" in inference
