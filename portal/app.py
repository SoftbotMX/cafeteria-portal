from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from portal.config import Settings, get_settings
from portal.db import crear_engine
from portal.sesiones import IrA
from portal.web import templates  # noqa: F401

BASE = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Portal de tutores")
    app.state.settings = settings
    app.state.engine = crear_engine(settings.database_url)
    # Las pruebas sustituyen estos transportes por simulados.
    app.state.tpv_transport = None
    app.state.conduit_transport = None
    app.mount("/estatico", StaticFiles(directory=str(BASE / "estatico")), name="estatico")

    @app.exception_handler(IrA)
    def _ir_a(_: Request, exc: IrA) -> RedirectResponse:
        return RedirectResponse(exc.url, status_code=303)

    @app.get("/salud")
    def salud() -> dict[str, bool]:
        return {"ok": True}

    from portal.rutas import acceso, tutor

    app.include_router(acceso.router)
    app.include_router(tutor.router)
    return app
