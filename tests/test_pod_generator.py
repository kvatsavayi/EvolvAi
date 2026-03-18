from pathlib import Path

from core.pod.generator import PodGenerator
from core.pod.pod import init_default_pods
from core.pod.lineage import utc_now_iso
from core.storage.db import Database


def test_pod_generator_selects_parent_from_external_signals(tmp_path: Path) -> None:
    db = Database(tmp_path / "gen_select.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    init_default_pods(db, tmp_path / "artifacts")

    # Favor pod_b using UI/environment signals only.
    db.insert_external_signal(
        signal_id="sig1",
        created_at=utc_now_iso(),
        request_id=None,
        pod_id="pod_b",
        request_type="general",
        signal_type="completion",
        value=1.0,
        metadata_json="{}",
    )
    db.insert_external_signal(
        signal_id="sig2",
        created_at=utc_now_iso(),
        request_id=None,
        pod_id="pod_b",
        request_type="general",
        signal_type="latency",
        value=200.0,
        metadata_json="{}",
    )

    gen = PodGenerator(db=db, artifact_root=tmp_path / "artifacts")
    parent = gen.select_parent_pod(request_type="general")
    assert parent == "pod_b"


def test_pod_generator_creates_variant_and_lineage(tmp_path: Path) -> None:
    db = Database(tmp_path / "gen_create.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    init_default_pods(db, tmp_path / "artifacts")

    gen = PodGenerator(db=db, artifact_root=tmp_path / "artifacts")
    created = gen.generate(count=1, request_type="general")

    assert len(created) == 1
    pod_id = created[0].pod_id

    pod_row = db.fetchone("SELECT pod_id FROM pods WHERE pod_id = ?", (pod_id,))
    assert pod_row is not None

    edge_row = db.fetchone(
        "SELECT edge_id FROM lineage_edges WHERE child_id = ? AND reason = 'mutation'",
        (f"pod:{pod_id}",),
    )
    assert edge_row is not None

    weight_row = db.fetchone(
        "SELECT weight FROM routing_weights_by_type WHERE request_type = ? AND pod_id = ?",
        ("general", pod_id),
    )
    assert weight_row is not None
