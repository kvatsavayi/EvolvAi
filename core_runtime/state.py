from __future__ import annotations

from apps.api.dependencies import AppState


def build_runtime_state() -> AppState:
    return AppState()
