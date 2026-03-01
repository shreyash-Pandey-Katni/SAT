"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from sat.config import SATConfig

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


def create_app(cfg: SATConfig) -> FastAPI:
    app = FastAPI(title="SAT — Activity Recorder", version="0.1.0")

    # stash config on app.state so routes can access it
    app.state.cfg = cfg
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Static files
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Routers
    from sat.web.routes.recordings import router as rec_router
    from sat.web.routes.executor import router as ex_router
    from sat.web.routes.cnl import router as cnl_router
    from sat.web.routes.ws import router as ws_router
    from sat.web.routes.ui import router as ui_router

    app.include_router(rec_router, prefix="/api/recordings", tags=["recordings"])
    app.include_router(ex_router, prefix="/api", tags=["executor"])
    app.include_router(cnl_router, prefix="/api", tags=["cnl"])
    app.include_router(ws_router, tags=["websocket"])
    app.include_router(ui_router, tags=["ui"])

    return app
