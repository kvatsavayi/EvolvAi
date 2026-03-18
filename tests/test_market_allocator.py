from pathlib import Path

from core.pod.pod import init_default_pods
from core.router.resources import ResourceAllocator
from core.router.router import Router
from core.storage.db import Database
from core.pod.lineage import utc_now_iso


def test_incubation_budget_for_generated_pods(tmp_path: Path) -> None:
    db = Database(tmp_path / "alloc.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    init_default_pods(db, tmp_path / "artifacts")
    db.insert_pod(
        pod_id="pod_gen_test",
        created_at=utc_now_iso(),
        is_enabled=True,
        config_json='{"pod_id":"pod_gen_test"}',
    )

    router = Router(["pod_a", "pod_b", "pod_gen_test"])
    allocator = ResourceAllocator(db=db, router=router)

    eligible = allocator.eligible_pods(request_type="general", pod_ids=["pod_a", "pod_b", "pod_gen_test"])
    assert "pod_gen_test" in eligible


def test_starvation_rule_blocks_underperformer(tmp_path: Path) -> None:
    db = Database(tmp_path / "starve.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    init_default_pods(db, tmp_path / "artifacts")

    # Pod A gets repeated retry/abandon with no completion.
    for i in range(6):
        db.insert_external_signal(
            signal_id=f"sig_retry_{i}",
            created_at=utc_now_iso(),
            request_id=None,
            pod_id="pod_a",
            request_type="general",
            signal_type="retry",
            value=1.0,
            metadata_json="{}",
        )

    router = Router(["pod_a", "pod_b"])
    allocator = ResourceAllocator(db=db, router=router)
    eligible = allocator.eligible_pods(request_type="general", pod_ids=["pod_a", "pod_b"])

    assert "pod_a" not in eligible
    assert "pod_b" in eligible


def test_traffic_cap_limits_overused_pod(tmp_path: Path) -> None:
    db = Database(tmp_path / "cap.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    init_default_pods(db, tmp_path / "artifacts")

    router = Router(["pod_a", "pod_b"])
    allocator = ResourceAllocator(db=db, router=router)

    # Force pod_a to appear saturated by assigned share and low cap.
    db.upsert_pod_resource_state(
        request_type="general",
        pod_id="pod_a",
        updated_at=utc_now_iso(),
        compute_budget=1.0,
        traffic_cap=0.1,
        incubation_budget=0,
        is_starved=False,
        assigned_requests=100,
        metadata_json="{}",
    )
    db.upsert_pod_resource_state(
        request_type="general",
        pod_id="pod_b",
        updated_at=utc_now_iso(),
        compute_budget=1.0,
        traffic_cap=0.9,
        incubation_budget=0,
        is_starved=False,
        assigned_requests=1,
        metadata_json="{}",
    )

    eligible = allocator.eligible_pods(request_type="general", pod_ids=["pod_a", "pod_b"])
    assert "pod_b" in eligible
