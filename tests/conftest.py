import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import make_url, text
from sqlalchemy.orm import Session

from portal.app import create_app
from portal.config import Settings
import portal.models  # noqa: F401  registra las tablas en Base.metadata
from portal.db import Base, crear_engine

URL_PRUEBAS = os.environ.get(
    "PORTAL_TEST_DATABASE_URL",
    "postgresql+psycopg://portal:portal@localhost:5433/portal_test",
)
# Llave fija solo para pruebas.
FERNET_PRUEBAS = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="


def _crear_bd_si_falta(url: str) -> None:
    u = make_url(url)
    admin = crear_engine(u.set(database="portal").render_as_string(hide_password=False))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as con:
        existe = con.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": u.database}
        ).scalar()
        if not existe:
            con.execute(text(f'CREATE DATABASE "{u.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine():
    _crear_bd_si_falta(URL_PRUEBAS)
    eng = crear_engine(URL_PRUEBAS)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    with Session(engine) as sesion:
        yield sesion
    tablas = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    if tablas:
        with engine.begin() as con:
            con.execute(text(f"TRUNCATE {tablas} RESTART IDENTITY CASCADE"))


@pytest.fixture
def app(engine):
    settings = Settings(
        database_url=URL_PRUEBAS, fernet_key=FERNET_PRUEBAS, cookie_secure=True
    )
    app = create_app(settings)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(app):
    return TestClient(app, base_url="https://cafeteria.test")
