import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from portal.config import Settings, get_settings
from portal.db import crear_engine
from portal.sesiones import IrA

BASE = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Portal de tutores")
    app.state.settings = settings
    app.state.engine = crear_engine(settings.database_url)
    # Las pruebas sustituyen estos transportes por simulados.
    app.state.tpv_transport = None
    app.state.conduit_transport = None
    app.state.mp_transport = None
    app.state.cache_menu = {}  # operador.id -> (instante, productos); ver tutor_tpv.menu
    app.state.cache_menu_candado = threading.Lock()
    app.mount("/estatico", StaticFiles(directory=str(BASE / "estatico")), name="estatico")

    @app.exception_handler(IrA)
    def _ir_a(request: Request, exc: IrA) -> Response:
        # Una petición HTMX seguiría el 303 por su cuenta y metería la página de entrada dentro
        # del fragmento: `HX-Redirect` hace que el navegador navegue de verdad.
        if request.headers.get("HX-Request"):
            return Response(status_code=200, headers={"HX-Redirect": exc.url})
        return RedirectResponse(exc.url, status_code=303)

    @app.get("/salud")
    def salud() -> dict[str, bool]:
        return {"ok": True}

    from portal.rutas import (
        acceso, admin, conduit_entrante, pedidos, recargas, recuperar, transferir, tutor)

    app.include_router(acceso.router)
    app.include_router(recuperar.router)
    app.include_router(admin.router)
    app.include_router(tutor.router)
    app.include_router(pedidos.router)
    app.include_router(transferir.router)
    app.include_router(recargas.router)
    app.include_router(conduit_entrante.router)
    return app
