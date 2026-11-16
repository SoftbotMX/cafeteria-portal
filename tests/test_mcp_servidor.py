"""Servidor MCP: consultas y acciones en dos pasos (preparar → confirmar) para una IA."""
import json
from datetime import date, datetime, timedelta, timezone
from itertools import count

import pytest
from fastapi.testclient import TestClient

from portal import cli
from portal.cifrado import cifrar
from portal.mcp_servidor import hash_token
from portal.models import McpConfirmacion, Operador, Recarga
from portal.reglas import ahora_utc
from tests.conftest import FERNET_PRUEBAS
from tests.fabricas import crear_operador, crear_tutor
from tests.mp_falso import MPFalso
from tests.test_cli import entorno  # noqa: F401  (fixture)
from tests.tpv_falso import TPVFalso

AHORA = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
TOKEN = "mcp_token-de-prueba"
TEL = "+5218341445807"
_ids = count(1)


@pytest.fixture
def op(db):
    return crear_operador(db, mp_access_token_cif=cifrar("APP_USR-token", FERNET_PRUEBAS),
                          mcp_token_hash=hash_token(TOKEN))


@pytest.fixture
def tpv(app):
    f = TPVFalso(secreto_tutor="secreto-tutor")
    f.hoy = date(2026, 10, 21)
    f.productos = [{"id": 10, "nombre": "Torta", "categoria": "Almuerzo", "precio": 45}]
    app.state.tpv_transport = f.transport
    return f


@pytest.fixture
def mp(app):
    f = MPFalso(token="APP_USR-token")
    app.state.mp_transport = f.transport
    return f


@pytest.fixture
def tutor(db, op):
    return crear_tutor(db, op, debe_cambiar=False)


@pytest.fixture
def hijos(tutor, tpv):
    return (tpv.agregar_hijo(tutor.tutor_ref, "Luis", disponible=200,
                             credenciales=[{"id": 1, "tipo": "nfc", "bloqueada": False}]),
            tpv.agregar_hijo(tutor.tutor_ref, "Ana", disponible=50))


@pytest.fixture
def c(app):
    app.dependency_overrides[ahora_utc] = lambda: AHORA
    with TestClient(app, base_url="https://cafeteria.test") as cliente:
        yield cliente
    app.dependency_overrides.pop(ahora_utc, None)


@pytest.fixture
def llamar(c, mp):
    def _llamar(herramienta, token=TOKEN, cliente=None, **args):
        r = (cliente or c).post("/mcp/", headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": next(_ids), "method": "tools/call",
                  "params": {"name": herramienta, "arguments": args}})
        assert r.status_code == 200, r.text
        res = r.json()["result"]
        if res.get("isError"):
            return {"error": res["content"][0]["text"]}
        return res.get("structuredContent") or json.loads(res["content"][0]["text"])
    return _llamar


def test_lista_de_herramientas(c):
    r = c.post("/mcp/", headers={"Accept": "application/json, text/event-stream"},
               json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    nombres = {t["name"] for t in r.json()["result"]["tools"]}
    assert nombres == {"hijos", "dias_para_pedir", "menu", "pedidos_por_recoger",
                       "preparar_pedido", "preparar_recarga", "preparar_transferencia",
                       "preparar_cancelacion", "preparar_permiso", "preparar_tarjeta", "confirmar"}


@pytest.mark.parametrize("token", ["", "mcp_otro"])
def test_sin_token_valido_no_hay_nada(hijos, llamar, token):
    assert "No autorizado" in llamar("hijos", token=token, telefono=TEL)["error"]


def test_host_ajeno(app, hijos, llamar, c):
    otro = TestClient(app, base_url="https://nadie.test")
    assert "No autorizado" in llamar("hijos", cliente=otro, telefono=TEL)["error"]


def test_numero_no_registrado(hijos, llamar):
    assert "no está registrado" in llamar("hijos", telefono="+5219990000000")["error"]
    assert "no está registrado" in llamar("hijos", telefono="+448341445807")["error"]


def test_consultas(hijos, llamar):
    r = llamar("hijos", telefono=TEL)
    assert [h["nombre"] for h in r["hijos"]] == ["Luis", "Ana"]
    assert r["hijos"][0]["disponible"] == "200" and r["hijos"][0]["tarjetas"][0]["bloqueada"] is False
    assert "2026-10-22" in llamar("dias_para_pedir", telefono=TEL)["dias"]
    assert llamar("menu", telefono=TEL)["productos"][0]["nombre"] == "Torta"


def test_pedido_en_dos_pasos(hijos, llamar, tpv):
    luis = hijos[0]["id"]
    p = llamar("preparar_pedido", telefono=TEL, alumno_id=luis, fecha="2026-10-22",
               productos=[{"producto_id": 10, "cantidad": 2}])
    assert "Total $90.00" in p["resumen"] and p["folio"]
    assert tpv.cuerpos_precompra == [], "preparar no escribe nada"
    r = llamar("confirmar", telefono=TEL, folio=p["folio"])
    assert r["estado"] == "ok" and "PC-" in r["mensaje"]
    assert llamar("confirmar", telefono=TEL, folio=p["folio"]) == r
    assert len(tpv.cuerpos_precompra) == 1


def test_pedido_invalido_no_prepara(hijos, llamar):
    luis = hijos[0]["id"]
    assert "pasó" in llamar("preparar_pedido", telefono=TEL, alumno_id=luis, fecha="2026-10-20",
                            productos=[{"producto_id": 10, "cantidad": 1}])["error"]
    assert "menú" in llamar("preparar_pedido", telefono=TEL, alumno_id=luis, fecha="2026-10-22",
                            productos=[{"producto_id": 99, "cantidad": 1}])["error"]


def test_hijo_ajeno(hijos, tpv, llamar):
    ajeno = tpv.agregar_hijo("OTRO", "Ajeno")
    assert "no es hijo" in llamar("preparar_permiso", telefono=TEL, alumno_id=ajeno["id"],
                                  permitir=False)["error"]


def test_folio_de_otro_papa(db, op, hijos, llamar, tpv):
    otro = crear_tutor(db, op, telefono="8340000000", debe_cambiar=False)
    tpv.agregar_hijo(otro.tutor_ref, "Beto")
    p = llamar("preparar_permiso", telefono=TEL, alumno_id=hijos[0]["id"], permitir=False)
    assert "desconocido" in llamar("confirmar", telefono="+528340000000", folio=p["folio"])["error"]
    assert tpv.cuerpos_permiso == []


def test_folio_vencido(db, hijos, llamar, tpv):
    p = llamar("preparar_permiso", telefono=TEL, alumno_id=hijos[0]["id"], permitir=False)
    conf = db.query(McpConfirmacion).one()
    conf.expira = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert "venció" in llamar("confirmar", telefono=TEL, folio=p["folio"])["error"]
    assert tpv.cuerpos_permiso == []


def test_incierto_se_reintenta_con_la_misma_llave(hijos, llamar, tpv):
    p = llamar("preparar_pedido", telefono=TEL, alumno_id=hijos[0]["id"], fecha="2026-10-22",
               productos=[{"producto_id": 10, "cantidad": 1}])
    tpv.fallar_en.add("precompra")
    assert llamar("confirmar", telefono=TEL, folio=p["folio"])["estado"] == "incierto"
    tpv.fallar_en.clear()
    assert llamar("confirmar", telefono=TEL, folio=p["folio"])["estado"] == "ok"
    assert len(tpv.pedidos) == 1


def test_recarga(db, hijos, llamar):
    p = llamar("preparar_recarga", telefono=TEL,
               reparto=[{"alumno_id": hijos[0]["id"], "importe": 100}])
    assert "Total $100.00" in p["resumen"]
    r = llamar("confirmar", telefono=TEL, folio=p["folio"])
    assert r["estado"] == "ok" and "https://mp.test/checkout" in r["mensaje"]
    assert db.query(Recarga).one().total == 100


def test_en_pausa_no_prepara_ni_repite_la_liga(db, hijos, llamar):
    """Con los pagos con tarjeta en pausa, el asistente no prepara recargas ni vuelve a dar la
    liga de una ya confirmada (Codex)."""
    from portal.models import Operador
    p = llamar("preparar_recarga", telefono=TEL, reparto=[{"alumno_id": hijos[0]["id"], "importe": 100}])
    assert "https://mp.test/checkout" in llamar("confirmar", telefono=TEL, folio=p["folio"])["mensaje"]
    db.query(Operador).update({"mp_pagos_activos": False})
    db.commit()
    r = llamar("confirmar", telefono=TEL, folio=p["folio"])
    assert r["estado"] == "rechazo" and "mp.test" not in r["mensaje"]
    assert "no están disponibles" in llamar("preparar_recarga", telefono=TEL,
                                             reparto=[{"alumno_id": hijos[0]["id"], "importe": 100}])["error"]


def test_recarga_fuera_de_limites(hijos, llamar):
    assert "pesos enteros" in llamar("preparar_recarga", telefono=TEL,
                                     reparto=[{"alumno_id": hijos[0]["id"], "importe": 20}])["error"]


def test_transferencia_cancelacion_permiso_tarjeta(hijos, llamar, tpv):
    luis, ana = hijos[0]["id"], hijos[1]["id"]
    p = llamar("preparar_transferencia", telefono=TEL, origen_id=luis, destino_id=ana, importe="50")
    assert "Disponible de Luis: $150.00" in llamar("confirmar", telefono=TEL, folio=p["folio"])["mensaje"]
    ped = llamar("preparar_pedido", telefono=TEL, alumno_id=luis, fecha="2026-10-22",
                 productos=[{"producto_id": 10, "cantidad": 1}])
    llamar("confirmar", telefono=TEL, folio=ped["folio"])
    (pendiente,) = llamar("pedidos_por_recoger", telefono=TEL, alumno_id=luis)["pedidos"]
    p = llamar("preparar_cancelacion", telefono=TEL, alumno_id=luis, pedido_id=pendiente["pedido_id"])
    assert "cancelaste" in llamar("confirmar", telefono=TEL, folio=p["folio"])["mensaje"]
    p = llamar("preparar_permiso", telefono=TEL, alumno_id=luis, permitir=False)
    llamar("confirmar", telefono=TEL, folio=p["folio"])
    assert hijos[0]["compra_sin_pedido"] is False
    p = llamar("preparar_tarjeta", telefono=TEL, alumno_id=luis, credencial_id=1, bloquear=True)
    llamar("confirmar", telefono=TEL, folio=p["folio"])
    assert hijos[0]["credenciales"][0]["bloqueada"] is True


def test_escrituras_apagadas(db, op, hijos, llamar):
    op.bot_escrituras = False
    db.commit()
    assert "no está disponible" in llamar("preparar_permiso", telefono=TEL,
                                          alumno_id=hijos[0]["id"], permitir=False)["error"]
    assert llamar("hijos", telefono=TEL)["hijos"]


def test_cli_crear_token_mcp(entorno, db, capsys):  # noqa: F811
    crear_operador(db, host="cafe.test")
    cli.main(["crear-token-mcp", "--host", "cafe.test"])
    token = capsys.readouterr().out.strip().splitlines()[-1]
    db.expire_all()
    guardado = db.query(Operador).one().mcp_token_hash
    assert token.startswith("mcp_") and guardado == hash_token(token) and token not in guardado


# -- revisión de Codex ---------------------------------------------------------------------------
def test_si_cambia_el_precio_no_se_confirma(hijos, llamar, tpv):
    p = llamar("preparar_pedido", telefono=TEL, alumno_id=hijos[0]["id"], fecha="2026-10-22",
               productos=[{"producto_id": 10, "cantidad": 2}])
    tpv.productos[0]["precio"] = 50
    r = llamar("confirmar", telefono=TEL, folio=p["folio"])
    assert r["estado"] == "rechazo" and "precio" in r["mensaje"]
    assert tpv.cuerpos_precompra == []


def test_confirmacion_en_curso_no_se_ejecuta_dos_veces(engine, db, hijos, llamar, tpv, monkeypatch):
    from sqlalchemy import text

    from portal import mcp_servidor
    monkeypatch.setattr(mcp_servidor, "ESPERA_CANDADO", 0.3)
    p = llamar("preparar_permiso", telefono=TEL, alumno_id=hijos[0]["id"], permitir=False)
    k = mcp_servidor.llave_candado(p["folio"])
    with engine.connect() as otra:
        otra.execute(text("SELECT pg_advisory_lock(:k)"), {"k": k})
        assert "en proceso" in llamar("confirmar", telefono=TEL, folio=p["folio"])["error"]
        otra.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": k})
        otra.commit()
    assert tpv.cuerpos_permiso == []
    assert llamar("confirmar", telefono=TEL, folio=p["folio"])["estado"] == "ok"


def test_resultado_definitivo_no_se_degrada(db, hijos, llamar, tpv):
    p = llamar("preparar_permiso", telefono=TEL, alumno_id=hijos[0]["id"], permitir=False)
    llamar("confirmar", telefono=TEL, folio=p["folio"])
    tpv.fallar_en.add("alumno/permiso")
    assert llamar("confirmar", telefono=TEL, folio=p["folio"])["estado"] == "ok"


def test_reintento_de_recarga_revalida_hijos_si_no_hubo_liga(db, tutor, hijos, llamar, mp, tpv):
    p = llamar("preparar_recarga", telefono=TEL,
               reparto=[{"alumno_id": hijos[1]["id"], "importe": 100}])
    mp.fallar.add("preferencia")
    assert llamar("confirmar", telefono=TEL, folio=p["folio"])["estado"] == "incierto"
    mp.fallar.clear()
    tpv.tutores[tutor.tutor_ref]["alumnos"].remove(hijos[1])
    assert "ya no es hijo" in llamar("confirmar", telefono=TEL, folio=p["folio"])["error"]
    assert db.query(Recarga).one().init_point is None


def test_incierto_no_vence(db, hijos, llamar, tpv):
    p = llamar("preparar_pedido", telefono=TEL, alumno_id=hijos[0]["id"], fecha="2026-10-22",
               productos=[{"producto_id": 10, "cantidad": 1}])
    tpv.fallar_en.add("precompra")
    assert llamar("confirmar", telefono=TEL, folio=p["folio"])["estado"] == "incierto"
    conf = db.query(McpConfirmacion).one()
    conf.expira = datetime.now(timezone.utc) - timedelta(hours=5)
    db.commit()
    tpv.fallar_en.clear()
    assert llamar("confirmar", telefono=TEL, folio=p["folio"])["estado"] == "ok"
    assert len({c["llave"] for c in tpv.cuerpos_precompra}) == 1
