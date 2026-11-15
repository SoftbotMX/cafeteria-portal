import os

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, make_url, text

from portal.db import crear_engine
from tests.conftest import URL_PRUEBAS

TABLAS = {
    "operador", "admin", "tutor", "sesion", "intento_login",
    "codigo_recuperacion", "bitacora", "config_pedidos", "dia_sin_servicio", "recarga", "bot_conversacion", "bot_mensaje", "mcp_confirmacion",
}


def test_migracion_crea_tablas():
    u = make_url(URL_PRUEBAS)
    url_mig = u.set(database="portal_mig").render_as_string(hide_password=False)
    admin = crear_engine(u.set(database="portal").render_as_string(hide_password=False))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as con:
        con.execute(text('DROP DATABASE IF EXISTS "portal_mig" WITH (FORCE)'))
        con.execute(text('CREATE DATABASE "portal_mig"'))
    admin.dispose()

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    cfg.set_main_option("sqlalchemy.url", url_mig.replace("%", "%%"))
    eng = crear_engine(url_mig)
    try:
        command.upgrade(cfg, "head")
        with eng.connect() as con:
            assert con.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0009"
        assert TABLAS <= set(inspect(eng).get_table_names())
        idx = {i["name"]: i for i in inspect(eng).get_indexes("tutor")}
        for n in ("uq_tutor_operador_telefono", "uq_tutor_operador_correo", "uq_tutor_operador_alta_llave"):
            assert idx[n]["unique"] and idx[n]["dialect_options"]["postgresql_where"]
        command.downgrade(cfg, "0001")
        tras = set(inspect(eng).get_table_names())
        assert not ({"config_pedidos", "dia_sin_servicio", "recarga", "bot_conversacion", "bot_mensaje", "mcp_confirmacion"} & tras) and "tutor" in tras
        command.downgrade(cfg, "base")
        assert not (TABLAS & set(inspect(eng).get_table_names()))
    finally:
        eng.dispose()
