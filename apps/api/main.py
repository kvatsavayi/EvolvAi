from __future__ import annotations

import os
from pathlib import Path

# Load .env from project root if python-dotenv is available
_env_path = Path(__file__).resolve().parents[2] / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=False)
    except ImportError:
        # Fallback: manual loading of simple KEY=VALUE lines
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#"):
                    continue
                # Strip inline comments
                _key_val = _line.split("#", 1)[0].strip()
                if "=" in _key_val:
                    _k, _v = _key_val.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())

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
