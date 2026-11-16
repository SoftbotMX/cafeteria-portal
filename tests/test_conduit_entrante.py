"""Destino de Conduit (Broker Mode) para el día 1: nadie queda silenciado y todo el que escribe
recibe la liga del portal. Firma: HMAC-SHA256(broker_secret, ts + "." + cuerpo)."""
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from tests.conduit_falso import ConduitFalso
from tests.fabricas import crear_operador

BASE = "/api/conduit/incoming"


@pytest.fixture
def op(db):
    return crear_operador(db)  # secreto de Conduit: "secreto-conduit"


@pytest.fixture
def conduit(app):
    f = ConduitFalso(secreto="secreto-conduit")
    app.state.conduit_transport = f.transport
    return f


@pytest.fixture
def c(app):
    return TestClient(app, base_url="https://cafeteria.test")


def _firmado(c, ruta, datos, secreto="secreto-conduit", ts=None):
    cuerpo = json.dumps(datos)
    ts = str(int(time.time()) if ts is None else ts)
    firma = hmac.new(secreto.encode(), f"{ts}.{cuerpo}".encode(), hashlib.sha256).hexdigest()
    return c.post(BASE + ruta, content=cuerpo, headers={
        "Content-Type": "application/json", "X-Conduit-Timestamp": ts,
        "X-Conduit-Signature": f"hmac-sha256={firma}"})


MENSAJE = {"message_id": "6f1c2a4e-0000-4000-8000-000000000001", "sender": "+5218341445807",
           "channel": "whatsapp", "text": "gracias", "received_at": "2026-10-22T12:00:00+00:00"}


def test_validate_acepta_a_todos(op, c):
    """Nunca se silencia a nadie: un rechazo en /validate cuenta para el silencio permanente."""
    for numero in ("+5218341445807", "+5219999999999"):
        r = _firmado(c, "/validate", {"sender": numero, "channel": "whatsapp"})
        assert r.status_code == 200 and r.json() == {"valid": True}


@pytest.mark.parametrize("ruta", ["/validate", "/message"])
def test_firma_mala_o_vieja_es_401(op, c, conduit, ruta):
    assert _firmado(c, ruta, MENSAJE, secreto="otro").status_code == 401
    assert _firmado(c, ruta, MENSAJE, ts=int(time.time()) - 400).status_code == 401
    r = c.post(BASE + ruta, json=MENSAJE)
    assert r.status_code == 401
    assert conduit.respuestas == []


def test_numero_no_registrado_recibe_el_aviso(op, c, conduit):
    r = _firmado(c, "/message", MENSAJE)
    assert r.status_code == 200 and r.json() == {"received": True}
    (resp,) = conduit.respuestas
    assert resp["message_id"] == MENSAJE["message_id"]
    assert "no está registrado" in resp["reply_text"]
    assert resp["cacheable"] is False


def test_mensaje_sin_texto_tambien_se_contesta(op, c, conduit):
    datos = {k: v for k, v in MENSAJE.items() if k != "text"} | {"media_url": "https://x/y.jpg"}
    assert _firmado(c, "/message", datos).status_code == 200
    assert len(conduit.respuestas) == 1


def test_reintenta_si_conduit_pide_reintentar(op, c, conduit):
    conduit.respuestas_status = [409, 503]
    _firmado(c, "/message", MENSAJE)
    assert len(conduit.respuestas) == 1


def test_cuerpo_invalido(op, c, conduit):
    assert _firmado(c, "/message", {"sender": "+52"}).status_code == 400
    assert conduit.respuestas == []


def test_operador_sin_conduit(db, c, conduit):
    crear_operador(db, conduit_url=None, conduit_client_id=None, conduit_secreto_cif=None)
    assert _firmado(c, "/validate", {"sender": "+52", "channel": "whatsapp"}).status_code == 404


def test_host_desconocido(app, conduit):
    otro = TestClient(app, base_url="https://nadie.test")
    assert _firmado(otro, "/validate", {"sender": "+52", "channel": "whatsapp"}).status_code == 404


def test_reintento_en_pausa_no_reenvia_la_liga(db, op, c, conduit):
    """WhatsApp reintenta un mensaje cuya respuesta guardada traía la liga de pago; con los pagos
    en pausa ya no se reenvía (Codex)."""
    from portal.models import BotMensaje, Operador, Recarga
    from tests.fabricas import crear_tutor
    tutor = crear_tutor(db, op)
    liga = "https://mp.test/checkout?pref_id=PREF-9"
    db.add(Recarga(operador_id=op.id, tutor_id=tutor.id, llave="ll-bot", total=100, reparto=[],
                   init_point=liga))
    db.add(BotMensaje(operador_id=op.id, message_id=MENSAJE["message_id"],
                      respuesta=f"Paga aquí: {liga}"))
    db.query(Operador).update({"mp_pagos_activos": False})
    db.commit()
    assert _firmado(c, "/message", MENSAJE).status_code == 200
    (resp,) = conduit.respuestas
    assert liga not in resp["reply_text"] and "no están disponibles" in resp["reply_text"]
