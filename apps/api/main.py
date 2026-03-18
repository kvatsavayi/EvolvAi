from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from apps.api.routes import router as v1_router

app = FastAPI(title="agent-pods", version="0.1.0")
app.include_router(v1_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


ui_root = Path(__file__).resolve().parents[1] / "ui"
ui_dist = ui_root / "dist"
if ui_dist.exists():
    app.mount("/", StaticFiles(directory=str(ui_dist), html=True), name="ui")
elif ui_root.exists():
    app.mount("/", StaticFiles(directory=str(ui_root), html=True), name="ui")
