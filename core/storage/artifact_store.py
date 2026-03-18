from __future__ import annotations

import os
import json
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple

from core.observability.canonical import canonical_json_dumps


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        base = os.getenv("APP_DATA_DIR")
        self.base_dir = Path(base).resolve() if base else None
        self.root.mkdir(parents=True, exist_ok=True)

    def _portable_path(self, path: Path) -> str:
        if self.base_dir is not None:
            try:
                return str(path.resolve().relative_to(self.base_dir))
            except Exception:
                return str(path.resolve())
        return str(path.resolve())

    def _resolve_path(self, path: str) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        if self.base_dir is not None:
            return (self.base_dir / p).resolve()
        return (self.root / p).resolve()

    def put_json(self, payload: Dict[str, Any]) -> Tuple[str, str]:
        # Artifact ids are write-instance ids; content identity is tracked by hashes in DB.
        artifact_id = f"art_{uuid.uuid4().hex[:16]}"
        path = self.root / f"{artifact_id}.json"
        path.write_text(canonical_json_dumps(payload), encoding="utf-8")
        return artifact_id, self._portable_path(path)

    def get_json(self, path: str) -> Dict[str, Any]:
        return json.loads(self._resolve_path(path).read_text(encoding="utf-8"))

    def write_json(self, artifact_id: str, payload: Dict[str, Any]) -> Path:
        # Backward-compatible helper for existing callsites.
        path = self.root / f"{artifact_id}.json"
        path.write_text(canonical_json_dumps(payload), encoding="utf-8")
        return path
