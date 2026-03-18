from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from urllib import request as urllib_request
import uuid
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from core.observability.canonical import canonical_sha256
from core.storage.artifact_store import ArtifactStore
from core.storage.db import Database


_ALLOWED_CAPABILITIES = {"read", "write", "search", "index"}
_DISALLOWED_PATTERNS = [
    re.compile(r"\bapi[_\s-]?key\b", re.IGNORECASE),
    re.compile(r"\bpassword\b", re.IGNORECASE),
    re.compile(r"\bssn\b", re.IGNORECASE),
    re.compile(r"\bprivate[_\s-]?key\b", re.IGNORECASE),
]


class WorkspaceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class WorkspaceLease:
    lease_id: str
    run_id: str
    attempt_id: str
    created_at: str
    expires_at: str
    capabilities: list[str]
    roots: list[str]
    budgets: dict[str, int]
    ops_used: int
    bytes_used: int
    files_used: int


class WorkspaceService:
    def __init__(self, *, db: Database, data_dir: Path) -> None:
        self.db = db
        self.data_dir = data_dir.resolve()
        self.workspace_root = (self.data_dir / "workspace").resolve()
        self.run_workspaces_root = (self.data_dir / "workspaces").resolve()
        self.knowledge_store = ArtifactStore((self.data_dir / "knowledge" / "artifacts").resolve())
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.run_workspaces_root.mkdir(parents=True, exist_ok=True)

    def _portable_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.data_dir).as_posix()
        except Exception:
            return path.name

    def create_workspace(self, *, run_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT run_id FROM runs WHERE run_id = ?", (run_id,))
        if row is None:
            raise WorkspaceError("run_not_found", "run_id was not found")
        workspace_id = f"ws_{run_id[-12:]}"
        root = (self.run_workspaces_root / workspace_id).resolve()
        root.mkdir(parents=True, exist_ok=True)
        manifest = self._build_workspace_manifest(workspace_id=workspace_id, run_id=run_id, root=root)
        self._save_workspace_manifest(root=root, manifest=manifest)
        return manifest

    def get_workspace(self, *, workspace_id: str) -> dict[str, Any]:
        root = self._workspace_root(workspace_id)
        manifest = self._load_workspace_manifest(root)
        refreshed = self._build_workspace_manifest(
            workspace_id=workspace_id,
            run_id=str(manifest.get("run_id", "")),
            root=root,
        )
        self._save_workspace_manifest(root=root, manifest=refreshed)
        return refreshed

    def workspace_write_file(self, *, workspace_id: str, run_id: str, path: str, content: str) -> dict[str, Any]:
        root = self._workspace_root(workspace_id)
        manifest = self._load_workspace_manifest(root)
        if str(manifest.get("run_id")) != run_id:
            raise WorkspaceError("workspace_run_mismatch", "run_id does not own this workspace")
        target = self._workspace_file_path(root=root, rel_path=path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        refreshed = self._build_workspace_manifest(workspace_id=workspace_id, run_id=run_id, root=root)
        self._save_workspace_manifest(root=root, manifest=refreshed)
        rel = str(target.relative_to(root))
        return {
            "workspace_id": workspace_id,
            "path": rel,
            "relative_path": rel,
            "bytes_written": len(content.encode("utf-8")),
            "sha256": refreshed["files"][rel]["sha256"],
        }

    def workspace_read_file(self, *, workspace_id: str, path: str) -> dict[str, Any]:
        root = self._workspace_root(workspace_id)
        target = self._workspace_file_path(root=root, rel_path=path)
        if not target.exists() or not target.is_file():
            raise WorkspaceError("not_found", "file does not exist")
        content = target.read_text(encoding="utf-8")
        return {
            "workspace_id": workspace_id,
            "path": str(target.relative_to(root)),
            "relative_path": str(target.relative_to(root)),
            "content": content,
            "bytes": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    def start_hello_service(
        self,
        *,
        workspace_id: str,
        host: str = "0.0.0.0",
        port: Optional[int] = None,
        health_path: str = "/hello",
    ) -> dict[str, Any]:
        root = self._workspace_root(workspace_id)
        app_path = (root / "app" / "main.py").resolve()
        if not app_path.exists():
            raise WorkspaceError("hello_service_missing_app", "workspace app/main.py not found")
        runtime_dir = (root / ".runtime").resolve()
        runtime_dir.mkdir(parents=True, exist_ok=True)
        status_path = runtime_dir / "hello_service.json"
        log_path = runtime_dir / "hello_service.log"

        existing = self.hello_service_status(workspace_id=workspace_id)
        if bool(existing.get("running")):
            return existing

        selected_port = int(port) if port is not None else self._find_available_port(start=8010, end=8099)
        cmd = [
            "python3",
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            host,
            "--port",
            str(selected_port),
        ]
        with log_path.open("a", encoding="utf-8") as lf:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                cwd=str(root),
                stdout=lf,
                stderr=lf,
                start_new_session=True,
            )
        started_at = datetime.now(timezone.utc).isoformat()
        status_payload = {
            "workspace_id": workspace_id,
            "pid": int(proc.pid),
            "host": host,
            "port": int(selected_port),
            "url": f"http://127.0.0.1:{selected_port}",
            "hello_url": f"http://127.0.0.1:{selected_port}{health_path}",
            "started_at": started_at,
            "cmd": cmd,
            "log_path": self._portable_path(log_path),
            "health_path": health_path,
        }
        status_path.write_text(json.dumps(status_payload, indent=2, sort_keys=True), encoding="utf-8")
        self._wait_for_hello(status_payload["hello_url"], timeout_seconds=8.0)
        status_payload["running"] = True
        return status_payload

    def hello_service_status(self, *, workspace_id: str) -> dict[str, Any]:
        root = self._workspace_root(workspace_id)
        status_path = (root / ".runtime" / "hello_service.json").resolve()
        if not status_path.exists():
            return {"workspace_id": workspace_id, "running": False}
        try:
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            return {"workspace_id": workspace_id, "running": False, "error": "invalid_status_file"}
        pid = int(status_payload.get("pid") or 0)
        running = self._is_pid_running(pid) if pid > 0 else False
        status_payload["running"] = running
        if running:
            status_payload["healthy"] = bool(self._probe_hello(str(status_payload.get("hello_url", ""))))
        else:
            status_payload["healthy"] = False
        return status_payload

    def create_lease(
        self,
        *,
        run_id: str,
        attempt_id: str,
        capabilities: list[str],
        roots: Optional[list[str]],
        budgets: dict[str, int],
        ttl_seconds: int,
    ) -> dict[str, Any]:
        attempt = self.db.get_run_attempt(attempt_id)
        if attempt is None:
            raise WorkspaceError("attempt_not_found", "attempt_id was not found")
        if str(attempt["run_id"]) != run_id:
            raise WorkspaceError("attempt_run_mismatch", "attempt_id does not belong to run_id")

        normalized_caps = sorted({str(c).strip().lower() for c in capabilities if str(c).strip()})
        if not normalized_caps:
            raise WorkspaceError("capabilities_required", "at least one capability is required")
        invalid_caps = [c for c in normalized_caps if c not in _ALLOWED_CAPABILITIES]
        if invalid_caps:
            raise WorkspaceError("invalid_capability", f"invalid capabilities: {invalid_caps}")

        effective_roots = roots or [f"workspace/{run_id}/{attempt_id}/scratch"]
        normalized_roots = [str(self._resolve_root(p)) for p in effective_roots]

        now = datetime.now(timezone.utc)
        created_at = now.isoformat()
        effective_ttl = max(1, int(ttl_seconds))
        expires_at = (now + timedelta(seconds=effective_ttl)).isoformat()
        lease_id = f"lease_{uuid.uuid4().hex[:16]}"
        normalized_budgets = {
            "max_bytes": max(1, int(budgets.get("max_bytes", 1_000_000))),
            "max_files": max(1, int(budgets.get("max_files", 256))),
            "max_ops": max(1, int(budgets.get("max_ops", 200))),
            "max_time_seconds": max(1, int(budgets.get("max_time_seconds", effective_ttl))),
        }

        self.db.insert_workspace_lease(
            lease_id=lease_id,
            run_id=run_id,
            attempt_id=attempt_id,
            created_at=created_at,
            expires_at=expires_at,
            capabilities_json=json.dumps(normalized_caps),
            roots_json=json.dumps(normalized_roots),
            budgets_json=json.dumps(normalized_budgets),
        )
        return {
            "lease_id": lease_id,
            "run_id": run_id,
            "attempt_id": attempt_id,
            "capabilities": normalized_caps,
            "roots": normalized_roots,
            "budgets": normalized_budgets,
            "created_at": created_at,
            "expiry": expires_at,
        }

    def read(self, *, lease_id: str, path: str) -> dict[str, Any]:
        lease = self._load_lease(lease_id)
        self._require_capability(lease, "read")
        target = self._resolve_under_roots(lease, path)
        if not target.exists() or not target.is_file():
            self._log_workspace_event(lease=lease, op_type="read", target_path=str(target), status="blocked", detail="not_found")
            raise WorkspaceError("not_found", "file does not exist")
        content = target.read_text(encoding="utf-8")
        self._consume_budget(lease=lease, op_type="read", target_path=str(target), bytes_delta=0, files_delta=0)
        portable = self._portable_path(target)
        return {"path": portable, "relative_path": portable, "content": content, "bytes": len(content.encode("utf-8"))}

    def write(self, *, lease_id: str, path: str, content: str) -> dict[str, Any]:
        lease = self._load_lease(lease_id)
        self._require_capability(lease, "write")
        target = self._resolve_under_roots(lease, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        is_new_file = not target.exists()
        byte_count = len(content.encode("utf-8"))
        self._consume_budget(
            lease=lease,
            op_type="write",
            target_path=str(target),
            bytes_delta=byte_count,
            files_delta=1 if is_new_file else 0,
            bytes_written_delta=byte_count,
        )
        target.write_text(content, encoding="utf-8")
        portable = self._portable_path(target)
        return {"path": portable, "relative_path": portable, "bytes_written": byte_count}

    def list(self, *, lease_id: str, path: str) -> dict[str, Any]:
        lease = self._load_lease(lease_id)
        if "read" not in lease.capabilities and "search" not in lease.capabilities:
            raise WorkspaceError("capability_missing", "list requires read or search capability")
        target = self._resolve_under_roots(lease, path)
        if not target.exists() or not target.is_dir():
            self._log_workspace_event(lease=lease, op_type="list", target_path=str(target), status="blocked", detail="not_found")
            raise WorkspaceError("not_found", "directory does not exist")
        items = []
        for child in sorted(target.iterdir(), key=lambda p: p.name):
            items.append(
                {
                    "name": child.name,
                    "path": self._portable_path(child),
                    "relative_path": self._portable_path(child),
                    "is_dir": child.is_dir(),
                    "size": (child.stat().st_size if child.is_file() else None),
                }
            )
        self._consume_budget(lease=lease, op_type="list", target_path=str(target), bytes_delta=0, files_delta=0)
        portable = self._portable_path(target)
        return {"path": portable, "relative_path": portable, "items": items}

    def commit_knowledge(
        self,
        *,
        lease_id: str,
        doc_key: str,
        title: Optional[str],
        summary: str,
        extracted_facts: list[str],
        source_artifact_ids: list[str],
    ) -> dict[str, Any]:
        lease = self._load_lease(lease_id)
        self._require_capability(lease, "index")
        if not doc_key.strip():
            raise WorkspaceError("doc_key_required", "doc_key is required")

        attempt = self.db.get_run_attempt(lease.attempt_id)
        if attempt is None:
            raise WorkspaceError("attempt_not_found", "attempt_id was not found")

        source_ids = sorted({str(x).strip() for x in source_artifact_ids if str(x).strip()})
        gate = self._evaluate_commit_gate(
            run_id=lease.run_id,
            passed_attempt=bool(attempt["pass"]),
            doc_key=doc_key,
            summary=summary,
            extracted_facts=extracted_facts,
            source_artifact_ids=source_ids,
        )

        commit_id = f"kcommit_{uuid.uuid4().hex[:16]}"
        created_at = datetime.now(timezone.utc).isoformat()
        if not gate["pass"]:
            reason = "; ".join(gate["failures"])
            self.db.insert_knowledge_commit(
                commit_id=commit_id,
                lease_id=lease.lease_id,
                run_id=lease.run_id,
                attempt_id=lease.attempt_id,
                created_at=created_at,
                passed=False,
                reason=reason,
                doc_key=doc_key,
                doc_id=None,
                version_id=None,
                source_artifact_ids_json=json.dumps(source_ids),
            )
            self._consume_budget(
                lease=lease,
                op_type="knowledge_commit",
                target_path=doc_key,
                bytes_delta=0,
                files_delta=0,
                commit_attempted_delta=1,
                commit_success_delta=0,
                source_artifact_ids=source_ids,
            )
            return {"commit_id": commit_id, "pass": False, "reason": reason, "gate": gate}

        doc = self.db.get_knowledge_doc_by_key(doc_key)
        if doc is None:
            doc_id = f"kdoc_{uuid.uuid4().hex[:16]}"
            version_num = 1
            self.db.upsert_knowledge_doc(
                doc_id=doc_id,
                doc_key=doc_key,
                created_at=created_at,
                updated_at=created_at,
                latest_version_num=version_num,
            )
        else:
            doc_id = str(doc["doc_id"])
            version_num = int(doc["latest_version_num"] or 0) + 1

        version_payload = {
            "doc_key": doc_key,
            "title": title,
            "summary": summary,
            "extracted_facts": extracted_facts,
            "source_artifact_ids": source_ids,
            "run_id": lease.run_id,
            "attempt_id": lease.attempt_id,
            "committed_at": created_at,
        }
        content_hash = canonical_sha256(version_payload)
        _, artifact_path = self.knowledge_store.put_json(version_payload)
        version_id = f"kver_{uuid.uuid4().hex[:16]}"
        self.db.insert_knowledge_version(
            version_id=version_id,
            doc_id=doc_id,
            version_num=version_num,
            created_at=created_at,
            title=title,
            summary=summary,
            extracted_facts_json=json.dumps(extracted_facts),
            source_artifact_ids_json=json.dumps(source_ids),
            content_hash=content_hash,
            artifact_path=artifact_path,
            created_by_run_id=lease.run_id,
            created_by_attempt_id=lease.attempt_id,
        )
        self.db.upsert_knowledge_doc(
            doc_id=doc_id,
            doc_key=doc_key,
            created_at=(str(doc["created_at"]) if doc is not None else created_at),
            updated_at=created_at,
            latest_version_num=version_num,
        )
        self.db.insert_knowledge_commit(
            commit_id=commit_id,
            lease_id=lease.lease_id,
            run_id=lease.run_id,
            attempt_id=lease.attempt_id,
            created_at=created_at,
            passed=True,
            reason=None,
            doc_key=doc_key,
            doc_id=doc_id,
            version_id=version_id,
            source_artifact_ids_json=json.dumps(source_ids),
        )
        self._consume_budget(
            lease=lease,
            op_type="knowledge_commit",
            target_path=doc_key,
            bytes_delta=0,
            files_delta=0,
            commit_attempted_delta=1,
            commit_success_delta=1,
            source_artifact_ids=source_ids,
        )
        return {
            "commit_id": commit_id,
            "pass": True,
            "doc_id": doc_id,
            "version_id": version_id,
            "version_num": version_num,
            "artifact_path": artifact_path,
            "gate": gate,
        }

    def search_knowledge(self, *, query: str, limit: int, lease_id: Optional[str] = None) -> dict[str, Any]:
        q = str(query or "").strip()
        if not q:
            return {"query": q, "items": []}
        if lease_id:
            lease = self._load_lease(lease_id)
            if "search" not in lease.capabilities and "read" not in lease.capabilities:
                raise WorkspaceError("capability_missing", "search requires search or read capability")
            self._consume_budget(
                lease=lease,
                op_type="knowledge_read",
                target_path=q,
                bytes_delta=0,
                files_delta=0,
                knowledge_reads_delta=1,
            )
        rows = self.db.search_knowledge_versions(query=q, limit=max(1, min(int(limit), 100)))
        return {
            "query": q,
            "items": [
                {
                    "doc_key": str(r["doc_key"]),
                    "doc_id": str(r["doc_id"]),
                    "version_id": str(r["version_id"]),
                    "version_num": int(r["version_num"]),
                    "title": str(r["title"] or ""),
                    "summary": str(r["summary"]),
                    "source_artifact_ids": json.loads(str(r["source_artifact_ids_json"] or "[]")),
                    "created_at": str(r["created_at"]),
                    "created_by_run_id": str(r["created_by_run_id"]),
                    "created_by_attempt_id": str(r["created_by_attempt_id"]),
                }
                for r in rows
            ],
        }

    def _evaluate_commit_gate(
        self,
        *,
        run_id: str,
        passed_attempt: bool,
        doc_key: str,
        summary: str,
        extracted_facts: list[str],
        source_artifact_ids: list[str],
    ) -> dict[str, Any]:
        failures: list[str] = []
        if not passed_attempt:
            failures.append("judge_pass_required")
        if not source_artifact_ids:
            failures.append("missing_source_artifact_ids")

        content_joined = " ".join([summary] + [str(x) for x in extracted_facts])
        if any(pattern.search(content_joined) for pattern in _DISALLOWED_PATTERNS):
            failures.append("contains_disallowed_content")

        request_row = self.db.fetchone(
            """
            SELECT req.user_input
            FROM runs r
            JOIN requests req ON req.request_id = r.request_id
            WHERE r.run_id = ?
            """,
            (run_id,),
        )
        request_text = str(request_row["user_input"]).lower() if request_row and request_row["user_input"] else ""
        req_terms = {tok for tok in re.findall(r"[a-z0-9]{4,}", request_text) if tok not in {"what", "with", "from", "that", "this"}}
        content_terms = set(re.findall(r"[a-z0-9]{4,}", content_joined.lower()))
        if req_terms and len(req_terms.intersection(content_terms)) == 0:
            failures.append("not_relevant_to_request")

        doc = self.db.get_knowledge_doc_by_key(doc_key)
        duplicate = False
        if doc is not None:
            latest = self.db.get_latest_knowledge_version(str(doc["doc_id"]))
            if latest is not None:
                existing_hash = str(latest["content_hash"])
                incoming_hash = canonical_sha256(
                    {
                        "doc_key": doc_key,
                        "summary": summary,
                        "extracted_facts": extracted_facts,
                        "source_artifact_ids": source_artifact_ids,
                    }
                )
                if existing_hash == incoming_hash:
                    duplicate = True
                    failures.append("duplicate_content")

        return {"pass": len(failures) == 0, "failures": failures, "duplicate": duplicate}

    def _resolve_root(self, root_path: str) -> Path:
        root = Path(root_path)
        if not root.is_absolute():
            if str(root).startswith("workspace/"):
                root = self.data_dir / root
            else:
                root = self.workspace_root / root
        resolved = root.resolve()
        if self.workspace_root != resolved and self.workspace_root not in resolved.parents:
            raise WorkspaceError("root_outside_workspace", "root must stay under data/workspace")
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _find_available_port(self, *, start: int, end: int) -> int:
        for p in range(start, end + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", p))
                    return p
                except OSError:
                    continue
        raise WorkspaceError("no_free_port", f"no free port in range {start}-{end}")

    def _is_pid_running(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _probe_hello(self, url: str) -> bool:
        if not url:
            return False
        try:
            with urllib_request.urlopen(url, timeout=1.5) as resp:  # noqa: S310
                _ = resp.read()
                code = int(resp.getcode() or 0)
            return code == 200
        except Exception:
            return False

    def _wait_for_hello(self, url: str, *, timeout_seconds: float) -> None:
        deadline = time.time() + max(1.0, float(timeout_seconds))
        while time.time() < deadline:
            if self._probe_hello(url):
                return
            time.sleep(0.25)
        raise WorkspaceError("hello_service_start_timeout", "service did not become healthy in time")

    def _workspace_root(self, workspace_id: str) -> Path:
        root = (self.run_workspaces_root / workspace_id).resolve()
        if self.run_workspaces_root not in root.parents and root != self.run_workspaces_root:
            raise WorkspaceError("workspace_invalid", "workspace_id is invalid")
        if not root.exists() or not root.is_dir():
            raise WorkspaceError("workspace_not_found", "workspace_id was not found")
        return root

    def _workspace_file_path(self, *, root: Path, rel_path: str) -> Path:
        candidate = (root / rel_path).resolve()
        if root != candidate and root not in candidate.parents:
            raise WorkspaceError("path_outside_workspace", "path is outside workspace")
        return candidate

    def _manifest_path(self, root: Path) -> Path:
        return root / "workspace_manifest.json"

    def _load_workspace_manifest(self, root: Path) -> dict[str, Any]:
        p = self._manifest_path(root)
        if not p.exists():
            raise WorkspaceError("manifest_missing", "workspace manifest not found")
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover
            raise WorkspaceError("manifest_invalid", f"workspace manifest invalid: {exc}") from exc

    def _save_workspace_manifest(self, *, root: Path, manifest: dict[str, Any]) -> None:
        self._manifest_path(root).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    def _build_workspace_manifest(self, *, workspace_id: str, run_id: str, root: Path) -> dict[str, Any]:
        files: dict[str, dict[str, Any]] = {}
        total_bytes = 0
        total_files = 0
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(root))
            if rel == "workspace_manifest.json":
                continue
            raw = p.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            size = len(raw)
            files[rel] = {"sha256": sha, "bytes": size}
            total_files += 1
            total_bytes += size
        return {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "root": self._portable_path(root),
            "stats": {"total_files": total_files, "total_bytes": total_bytes},
            "files": files,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _load_lease(self, lease_id: str) -> WorkspaceLease:
        row = self.db.get_workspace_lease(lease_id)
        if row is None:
            raise WorkspaceError("lease_not_found", "lease_id was not found")
        lease = WorkspaceLease(
            lease_id=str(row["lease_id"]),
            run_id=str(row["run_id"]),
            attempt_id=str(row["attempt_id"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            capabilities=json.loads(str(row["capabilities_json"] or "[]")),
            roots=json.loads(str(row["roots_json"] or "[]")),
            budgets=json.loads(str(row["budgets_json"] or "{}")),
            ops_used=int(row["ops_used"] or 0),
            bytes_used=int(row["bytes_used"] or 0),
            files_used=int(row["files_used"] or 0),
        )
        now = datetime.now(timezone.utc)
        if now > datetime.fromisoformat(lease.expires_at):
            raise WorkspaceError("lease_expired", "lease has expired")
        if (now - datetime.fromisoformat(lease.created_at)).total_seconds() > float(
            lease.budgets.get("max_time_seconds", 0)
        ):
            raise WorkspaceError("budget_exceeded_time", "lease time budget exceeded")
        return lease

    def _require_capability(self, lease: WorkspaceLease, capability: str) -> None:
        if capability not in lease.capabilities:
            raise WorkspaceError("capability_missing", f"capability '{capability}' required")

    def _resolve_under_roots(self, lease: WorkspaceLease, path: str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            base = Path(lease.roots[0])
            candidate = base / candidate
        resolved = candidate.resolve()
        for root in lease.roots:
            root_resolved = Path(root).resolve()
            if resolved == root_resolved or root_resolved in resolved.parents:
                return resolved
        raise WorkspaceError("path_outside_roots", "path is outside lease roots")

    def _consume_budget(
        self,
        *,
        lease: WorkspaceLease,
        op_type: str,
        target_path: str,
        bytes_delta: int,
        files_delta: int,
        bytes_written_delta: int = 0,
        knowledge_reads_delta: int = 0,
        commit_attempted_delta: int = 0,
        commit_success_delta: int = 0,
        source_artifact_ids: Optional[list[str]] = None,
    ) -> None:
        max_ops = int(lease.budgets.get("max_ops", 0))
        max_bytes = int(lease.budgets.get("max_bytes", 0))
        max_files = int(lease.budgets.get("max_files", 0))
        if lease.ops_used + 1 > max_ops:
            self._log_workspace_event(lease=lease, op_type=op_type, target_path=target_path, status="blocked", detail="budget_exceeded_ops")
            raise WorkspaceError("budget_exceeded_ops", "operation budget exceeded")
        if lease.bytes_used + int(bytes_delta) > max_bytes:
            self._log_workspace_event(lease=lease, op_type=op_type, target_path=target_path, status="blocked", detail="budget_exceeded_bytes")
            raise WorkspaceError("budget_exceeded_bytes", "byte budget exceeded")
        if lease.files_used + int(files_delta) > max_files:
            self._log_workspace_event(lease=lease, op_type=op_type, target_path=target_path, status="blocked", detail="budget_exceeded_files")
            raise WorkspaceError("budget_exceeded_files", "file budget exceeded")

        self.db.update_workspace_lease_usage(
            lease_id=lease.lease_id,
            ops_delta=1,
            bytes_delta=int(bytes_delta),
            files_delta=int(files_delta),
        )
        self.db.apply_attempt_workspace_metrics(
            attempt_id=lease.attempt_id,
            workspace_ops_delta=1,
            bytes_written_delta=int(bytes_written_delta),
            knowledge_reads_delta=int(knowledge_reads_delta),
            knowledge_commit_attempted_delta=int(commit_attempted_delta),
            knowledge_commits_delta=int(commit_success_delta),
            source_artifact_ids=source_artifact_ids,
        )
        self._log_workspace_event(
            lease=lease,
            op_type=op_type,
            target_path=target_path,
            status="allowed",
            detail=None,
            bytes_delta=int(bytes_delta),
            files_delta=int(files_delta),
        )

    def _log_workspace_event(
        self,
        *,
        lease: WorkspaceLease,
        op_type: str,
        target_path: str,
        status: str,
        detail: Optional[str],
        bytes_delta: int = 0,
        files_delta: int = 0,
    ) -> None:
        self.db.insert_workspace_op_event(
            op_id=f"wop_{uuid.uuid4().hex[:16]}",
            lease_id=lease.lease_id,
            run_id=lease.run_id,
            attempt_id=lease.attempt_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            op_type=op_type,
            target_path=target_path,
            bytes_delta=int(bytes_delta),
            files_delta=int(files_delta),
            status=status,
            detail=detail,
        )
