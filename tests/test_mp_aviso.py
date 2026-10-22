"""El aviso de MercadoPago y el barrido: nunca se le cree al aviso, siempre se consulta el pago."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from portal import barrido, recargas
from portal.cifrado import cifrar
from portal.models import Recarga
from tests.conftest import FERNET_PRUEBAS
from tests.fabricas import crear_operador, crear_tutor
from tests.mp_falso import MPFalso
from tests.tpv_falso import TPVFalso


@pytest.fixture
def op(db):
    return crear_operador(db, mp_access_token_cif=cifrar("TEST-token", FERNET_PRUEBAS))


@pytest.fixture
def tpv(app):
    f = TPVFalso(secreto_tutor="secreto-tutor")
    app.state.tpv_transport = f.transport
    return f


@pytest.fixture
def mp(app):
    f = MPFalso()
    app.state.mp_transport = f.transport
    return f


@pytest.fixture
def c(app):
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


@pytest.fixture
def rec(db, app, op, tpv, mp):
    t = crear_tutor(db, op, debe_cambiar=False)
    h = tpv.agregar_hijo(t.tutor_ref, "Luis")
    r = recargas.crear(db, app, op, t, [{"alumno_id": h["id"], "nombre": "Luis", "importe": 100}],
                       Decimal(100), "llave-aviso-1", "https://cafeteria.test")
    r.hijo = h
    return r


def test_aviso_webhook_acredita(c, db, rec, mp, tpv):
    pid = mp.pagar(rec.id, 100)
    r = c.post(f"/mp/aviso?data.id={pid}&type=payment",
               json={"type": "payment", "action": "payment.created", "data": {"id": pid}})
    assert r.status_code == 200
    db.refresh(rec)
    assert rec.estado == "acreditada" and rec.hijo["disponible"] == 100


def test_aviso_ipn_por_query(c, db, rec, mp):
    pid = mp.pagar(rec.id, 100)
    assert c.post(f"/mp/aviso?topic=payment&id={pid}").status_code == 200
    db.refresh(rec)
    assert rec.estado == "acreditada"


def test_aviso_solo_en_el_cuerpo(c, db, rec, mp):
    pid = mp.pagar(rec.id, 100)
    assert c.post("/mp/aviso", json={"type": "payment", "data": {"id": pid}}).status_code == 200
    db.refresh(rec)
    assert rec.estado == "acreditada"


def test_aviso_falso_no_acredita(c, db, rec, mp, tpv):
    """El aviso dice «aprobado» por su cuenta: no importa, MP dice que no existe."""
    r = c.post("/mp/aviso?data.id=999&type=payment",
               json={"type": "payment", "data": {"id": "999", "status": "approved"}})
    assert r.status_code == 200 and tpv.cuerpos_recarga == []
    db.refresh(rec)
    assert rec.estado == "pendiente"


@pytest.mark.parametrize("q", ["topic=merchant_order&id=5", "type=plan&data.id=5", "", "data.id=abc&type=payment"])
def test_otros_avisos_se_ignoran(c, mp, op, q):
    assert c.post(f"/mp/aviso?{q}").status_code == 200
    assert mp.consultas == []


def test_aviso_con_mp_caido_pide_reintento(c, db, rec, mp):
    pid = mp.pagar(rec.id, 100)
    mp.fallar.add("pago")
    assert c.post(f"/mp/aviso?data.id={pid}&type=payment").status_code == 503
    db.refresh(rec)
    assert rec.estado == "pendiente"


def test_aviso_cuerpo_basura(c, op, mp):
    r = c.post("/mp/aviso", content=b"\xff no json", headers={"Content-Type": "application/json"})
    assert r.status_code == 200


def test_aviso_host_desconocido(app, mp):
    otro = TestClient(app, base_url="https://nadie.test")
    assert otro.post("/mp/aviso?data.id=1&type=payment").status_code == 404


# -- barrido ------------------------------------------------------------------
def test_barrido_acredita_lo_que_el_tpv_no_tomo(db, app, op, rec, mp, tpv):
    tpv.fallar_en.add("recarga")
    recargas.procesar_pago(db, app, op, mp.pagar(rec.id, 100))
    db.commit()
    assert rec.estado == "aprobada"
    tpv.fallar_en.clear()
    assert barrido.una_vuelta(app) == 1
    db.refresh(rec)
    assert rec.estado == "acreditada"


def test_barrido_busca_pagos_sin_aviso(db, app, rec, mp):
    mp.pagar(rec.id, 100)
    rec.creada = datetime.now(timezone.utc) - timedelta(minutes=11)
    db.commit()
    barrido.una_vuelta(app)
    db.refresh(rec)
    assert rec.estado == "acreditada"


def test_barrido_no_molesta_al_que_esta_pagando(db, app, rec, mp):
    mp.pagar(rec.id, 100)
    assert barrido.una_vuelta(app) == 0
    db.refresh(rec)
    assert rec.estado == "pendiente"


def test_barrido_vence_las_viejas(db, app, rec, mp):
    rec.creada = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()
    barrido.una_vuelta(app)
    db.refresh(rec)
    assert rec.estado == "vencida"


def test_barrido_sigue_si_una_falla(db, app, op, rec, mp, tpv):
    t = db.get(type(rec), rec.id)
    otra = Recarga(operador_id=op.id, tutor_id=t.tutor_id, llave="otra-llave-1", total=Decimal(50),
                   reparto=[], creada=datetime.now(timezone.utc) - timedelta(hours=1))
    db.add(otra)
    rec.creada = datetime.now(timezone.utc) - timedelta(minutes=11)
    mp.pagar(rec.id, 100)
    db.commit()
    mp.fallar.add("buscar")
    barrido.una_vuelta(app)  # no lanza
    mp.fallar.clear()
    barrido.una_vuelta(app)
    db.refresh(rec)
    assert rec.estado == "acreditada"


def test_rutas_que_esperan_red_no_son_async(app):
    """Una ruta `async` que hace HTTP o SQL síncrono congela el servidor entero (Codex P1)."""
    import inspect

    from portal.rutas import recargas as rutas
    for fn in (rutas.revisar, rutas.confirmar, rutas.aviso_mp):
        fuente = inspect.getsource(fn)
        assert "run_in_threadpool" in fuente or not inspect.iscoroutinefunction(fn), fn.__name__


def test_barrido_no_se_atasca_con_las_mismas(db, app, op, rec, mp, tpv, monkeypatch):
    """Con el TPV de una caído, las demás siguen: primero las que llevan más sin intentarse."""
    monkeypatch.setattr(barrido, "LOTE", 1)
    tpv.fallar_en.add("recarga")
    recargas.procesar_pago(db, app, op, mp.pagar(rec.id, 100))
    db.commit()
    t = db.get(type(rec), rec.id)
    otra = recargas.crear(db, app, op, db.get(__import__("portal.models", fromlist=["Tutor"]).Tutor, t.tutor_id),
                          [{"alumno_id": rec.hijo["id"], "nombre": "Luis", "importe": 60}],
                          Decimal(60), "llave-otra-2", "https://cafeteria.test")
    otra.creada = datetime.now(timezone.utc) - timedelta(minutes=11)
    mp.pagar(otra.id, 60)
    db.commit()
    barrido.una_vuelta(app)  # intenta `rec` (TPV caído)
    tpv.fallar_en.clear()
    barrido.una_vuelta(app)  # ahora le toca a `otra`, no otra vez a `rec`
    db.refresh(otra)
    assert otra.estado == "acreditada"


def test_conciliacion_final_encuentra_pagos_de_mas_sin_aviso(db, app, op, rec, mp, tpv):
    recargas.procesar_pago(db, app, op, mp.pagar(rec.id, 100))
    db.commit()
    extra = mp.pagar(rec.id, 100)  # su aviso se perdió
    rec.liga_vence = datetime.now(timezone.utc) - timedelta(minutes=16)
    db.commit()
    barrido.una_vuelta(app)
    db.refresh(rec)
    assert rec.pagos_extra == [extra] and rec.conciliada and rec.estado == "acreditada"
    antes = mp.busquedas
    barrido.una_vuelta(app)
    assert mp.busquedas == antes, "una sola conciliación por recarga"


def test_no_concilia_mientras_la_liga_sigue_viva(db, app, op, rec, mp):
    recargas.procesar_pago(db, app, op, mp.pagar(rec.id, 100))
    db.commit()
    barrido.una_vuelta(app)
    db.refresh(rec)
    assert not rec.conciliada


def test_un_error_inesperado_igual_marca_el_intento(db, app, op, rec, mp, tpv, monkeypatch):
    tpv.fallar_en.add("recarga")
    recargas.procesar_pago(db, app, op, mp.pagar(rec.id, 100))
    db.commit()
    antes = db.get(Recarga, rec.id).actualizada

    def revienta(*_a, **_k):
        raise RuntimeError("credenciales ilegibles")
    monkeypatch.setattr(recargas, "acreditar", revienta)
    barrido.una_vuelta(app)
    db.expire_all()
    assert db.get(Recarga, rec.id).actualizada > antes


def test_concilia_tras_el_vencimiento_real_de_la_liga(db, app, op, rec, mp):
    """Liga creada tarde (MP falló al principio): manda su vencimiento, no `creada` (Codex)."""
    recargas.procesar_pago(db, app, op, mp.pagar(rec.id, 100))
    rec.creada = datetime.now(timezone.utc) - timedelta(hours=2)
    rec.liga_vence = datetime.now(timezone.utc) + timedelta(minutes=20)
    db.commit()
    barrido.una_vuelta(app)
    db.refresh(rec)
    assert not rec.conciliada
    rec.liga_vence = datetime.now(timezone.utc) - timedelta(minutes=16)
    db.commit()
    barrido.una_vuelta(app)
    db.refresh(rec)
    assert rec.conciliada


def test_barrido_borra_mensajes_viejos_del_bot(db, app, op):
    from portal.models import BotMensaje
    db.add_all([BotMensaje(operador_id=op.id, message_id="viejo",
                           creado=datetime.now(timezone.utc) - timedelta(days=3)),
                BotMensaje(operador_id=op.id, message_id="nuevo")])
    db.commit()
    barrido.una_vuelta(app)
    db.expire_all()
    assert [m.message_id for m in db.query(BotMensaje)] == ["nuevo"]
