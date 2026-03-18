from __future__ import annotations

from typing import Any, Dict


def git_commit(*, message: str, paths: list[str] | None = None) -> Dict[str, Any]:
    # v1 safe-autonomy mock: commit planning record only, no actual git side-effect.
    return {
        "status": "staged_commit",
        "message": message,
        "paths": paths or [],
        "dry_run": True,
    }


def deploy_staging(*, service: str, version: str) -> Dict[str, Any]:
    # v1 safe-autonomy mock deploy hook.
    return {
        "status": "staged_deploy",
        "service": service,
        "version": version,
        "environment": "staging",
        "dry_run": True,
    }
