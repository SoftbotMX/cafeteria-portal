from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from portal.config import Settings, get_settings
from portal.db import crear_engine

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "plantillas"))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Portal de tutores")
    app.state.settings = settings
    app.state.engine = crear_engine(settings.database_url)
    # Las pruebas sustituyen estos transportes por simulados.
    app.state.tpv_transport = None
    app.state.conduit_transport = None
    app.mount("/estatico", StaticFiles(directory=str(BASE / "estatico")), name="estatico")

    @app.get("/salud")
    def salud() -> dict[str, bool]:
        return {"ok": True}

    return app
