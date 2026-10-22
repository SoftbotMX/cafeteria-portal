"""El bot de WhatsApp guiado, de punta a punta: mensaje firmado de Conduit → bot → respuesta por
/api/broker/reply. Las reglas son las de la web (núcleo `portal.acciones`)."""
import hashlib
import hmac
import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from portal.cifrado import cifrar
from portal.models import BotConversacion, Recarga
from portal.reglas import ahora_utc
from tests.conduit_falso import ConduitFalso
from tests.conftest import FERNET_PRUEBAS
from tests.fabricas import crear_operador, crear_tutor
from tests.mp_falso import MPFalso
from tests.tpv_falso import TPVFalso

AHORA = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)  # 06:00 en CDMX
NUMERO = "+5218341445807"


@pytest.fixture
def op(db):
    return crear_operador(db, mp_access_token_cif=cifrar("APP_USR-token", FERNET_PRUEBAS))


@pytest.fixture
def tpv(app):
    f = TPVFalso(secreto_tutor="secreto-tutor")
    f.hoy = date(2026, 10, 21)
    f.productos = [{"id": 10, "nombre": "Torta", "categoria": "Almuerzo", "precio": 45},
                   {"id": 11, "nombre": "Agua", "categoria": "Bebidas", "precio": 15}]
    app.state.tpv_transport = f.transport
    return f


@pytest.fixture
def mp(app):
    f = MPFalso(token="APP_USR-token")
    app.state.mp_transport = f.transport
    return f


@pytest.fixture
def conduit(app):
    f = ConduitFalso(secreto="secreto-conduit")
    app.state.conduit_transport = f.transport
    return f


@pytest.fixture
def c(app):
    app.dependency_overrides[ahora_utc] = lambda: AHORA
    yield TestClient(app, base_url="https://cafeteria.test")
    app.dependency_overrides.pop(ahora_utc, None)


@pytest.fixture
def tutor(db, op):
    return crear_tutor(db, op, debe_cambiar=False)


@pytest.fixture
def hijos(tutor, tpv):
    luis = tpv.agregar_hijo(tutor.tutor_ref, "Luis", disponible=200,
                            credenciales=[{"id": 1, "tipo": "nfc", "bloqueada": False}])
    ana = tpv.agregar_hijo(tutor.tutor_ref, "Ana", disponible=50)
    return luis, ana


@pytest.fixture
def escribir(c, conduit, mp):
    def _escribir(texto, numero=NUMERO, message_id=None):
        cuerpo = json.dumps({"message_id": message_id or str(uuid.uuid4()), "sender": numero,
                             "channel": "whatsapp", "text": texto})
        ts = str(int(time.time()))
        firma = hmac.new(b"secreto-conduit", f"{ts}.{cuerpo}".encode(), hashlib.sha256).hexdigest()
        r = c.post("/api/conduit/incoming/message", content=cuerpo, headers={
            "Content-Type": "application/json", "X-Conduit-Timestamp": ts,
            "X-Conduit-Signature": f"hmac-sha256={firma}"})
        assert r.status_code == 200
        return conduit.respuestas[-1]["reply_text"]
    return _escribir


def test_saludo_y_saldo(hijos, escribir):
    r = escribir("hola")
    assert "Hola Ana." in r and "1 Ver saldo" in r and "7 Tarjetas" in r
    r = escribir("1")
    assert "Luis: $200.00 disponible" in r and "Ana: $50.00 disponible" in r
    assert "¿Qué deseas hacer?" in r


def test_numero_no_registrado(hijos, escribir):
    assert "no está registrado" in escribir("hola", numero="+5219990000000")


def test_respuesta_invalida_repite(hijos, escribir):
    escribir("hola")
    assert "No entendí" in escribir("99")


def test_recargar_a_todos_por_igual(db, hijos, escribir, mp):
    escribir("hola")
    r = escribir("2")
    assert "3 Todos por igual" in r
    assert "¿Cuánto a cada uno?" in escribir("3")
    r = escribir("100")
    assert "Total: $200.00" in r and "1 Confirmar" in r
    r = escribir("1")
    assert "https://mp.test/checkout" in r and "30 minutos" in r
    rec = db.query(Recarga).one()
    assert rec.total == 200 and len(rec.reparto) == 2


def test_recargar_respeta_el_minimo_y_cancelar_no_crea_nada(db, hijos, escribir):
    escribir("hola")
    escribir("2")
    escribir("1")
    assert "$50" in escribir("30")
    assert "Total: $50.00" in escribir("50")
    assert "no se hizo nada" in escribir("2")
    assert db.query(Recarga).count() == 0


def _pedir_torta(escribir, dia="2"):
    escribir("hola")
    escribir("3")
    r = escribir("1")  # Luis
    assert "1 Hoy" in r and "2 Mañana" in r
    r = escribir(dia)
    assert "1 Torta $45.00" in r
    assert "2 × Torta" in escribir("1 2")
    assert "1 × Agua" in escribir("2")
    r = escribir("listo")
    assert "Total: $105.00" in r
    return escribir("1")


def test_pedir_para_manana(hijos, escribir, tpv):
    r = _pedir_torta(escribir)
    assert "¡Listo!" in r and "PC-" in r
    (cuerpo,) = tpv.cuerpos_precompra
    assert cuerpo["fecha_entrega"] == "2026-10-22" and cuerpo["alumno_id"] == hijos[0]["id"]
    assert sorted((i["producto_id"], i["cantidad"]) for i in cuerpo["items"]) == [(10, 2), (11, 1)]


def test_pedir_sin_respuesta_reintenta_con_la_misma_llave(hijos, escribir, tpv):
    tpv.fallar_en.add("precompra")
    r = _pedir_torta(escribir)
    assert "Responde 1 para reintentar" in r
    tpv.fallar_en.clear()
    assert "¡Listo!" in escribir("1")
    assert len({c["llave"] for c in tpv.cuerpos_precompra}) == 1
    assert len(tpv.pedidos) == 1


def test_cancelar_un_pedido(hijos, escribir, tpv):
    _pedir_torta(escribir)
    escribir("4")
    r = escribir("1")  # Luis
    assert "PC-" in r and "$105.00" in r
    assert "¿Cancelar el pedido" in escribir("1")
    assert "cancelaste el pedido" in escribir("1")
    assert tpv.pedidos[0]["estado"] == "cancelada"


def test_transferir(hijos, escribir):
    escribir("hola")
    r = escribir("5")
    assert "1 Luis" in r and "2 Ana" in r
    assert "¿Cuánto le pasas de Luis a Ana?" in escribir("1")
    assert "Pasar $50.00 de Luis a Ana" in escribir("50")
    r = escribir("1")
    assert "Disponible de Luis: $150.00" in r and "de Ana: $100.00" in r


def test_transferir_mas_de_lo_disponible(hijos, escribir):
    escribir("hola")
    escribir("5")
    escribir("2")  # Ana, con $50
    escribir("80")
    assert "no tiene tanto" in escribir("1")


def test_compras_sin_pedido(hijos, escribir, tpv):
    escribir("hola")
    escribir("6")
    r = escribir("1")
    assert "puede comprar sin pedido" in r and "1 Que sólo recoja lo que pidas" in r
    assert "sólo podrá recoger lo que pidas" in escribir("1")
    assert hijos[0]["compra_sin_pedido"] is False


def test_bloquear_tarjeta(hijos, escribir):
    escribir("hola")
    escribir("7")
    r = escribir("1")  # Luis
    assert "Tarjeta" in r and "Activa" in r
    assert "¿Bloquear" in escribir("1")
    assert "bloqueada" in escribir("1")
    assert hijos[0]["credenciales"][0]["bloqueada"] is True


def test_sin_tarjeta(hijos, escribir):
    escribir("hola")
    escribir("7")
    assert "no tiene tarjeta" in escribir("2")


def test_escrituras_apagadas(db, op, hijos, escribir):
    op.bot_escrituras = False
    db.commit()
    escribir("hola")
    assert "sólo puedes ver el saldo" in escribir("3")
    assert "Luis: $200.00" in escribir("1")


def test_menu_en_cualquier_momento(hijos, escribir):
    escribir("hola")
    escribir("3")
    assert "¿Qué deseas hacer?" in escribir("Menú")


def test_conversacion_se_olvida_a_los_30_minutos(db, hijos, escribir):
    escribir("hola")
    escribir("3")
    conv = db.query(BotConversacion).one()
    conv.actualizada = datetime.now(timezone.utc) - timedelta(minutes=31)
    db.commit()
    assert "¿Qué deseas hacer?" in escribir("1")


def test_un_solo_hijo_no_pregunta_de_quien(db, tutor, tpv, escribir):
    tpv.agregar_hijo(tutor.tutor_ref, "Luis", disponible=200)
    escribir("hola")
    assert "¿Para qué día" in escribir("3")


def test_transferir_con_un_solo_hijo(db, tutor, tpv, escribir):
    tpv.agregar_hijo(tutor.tutor_ref, "Luis", disponible=200)
    escribir("hola")
    assert "dos hijos o más" in escribir("5")


# -- revisión de Codex ---------------------------------------------------------------------------
def test_mensaje_repetido_no_avanza_ni_confirma(hijos, escribir, tpv, conduit):
    """El mismo `message_id` dos veces: la segunda recibe la misma respuesta y no se toma como
    la confirmación del paso siguiente."""
    escribir("hola")
    escribir("6")
    mid = str(uuid.uuid4())
    primera = escribir("1", message_id=mid)  # elige a Luis → pide confirmar
    assert "1 Que sólo recoja" in primera
    assert escribir("1", message_id=mid) == primera
    assert tpv.cuerpos_permiso == []


def test_un_mensaje_espera_al_otro_del_mismo_papa(engine, db, op, tutor, hijos, escribir):
    """Candado por papá durante TODO el mensaje (aunque las acciones hagan commit)."""
    import threading

    from sqlalchemy import text

    from portal.bot import llave_candado
    escribir("hola")
    respuestas = []
    with engine.connect() as otra:
        otra.execute(text("SELECT pg_advisory_lock(:k)"), {"k": llave_candado(op.id, tutor.telefono)})
        h = threading.Thread(target=lambda: respuestas.append(escribir("1")))
        h.start()
        h.join(0.5)
        assert h.is_alive(), "debió esperar el candado"
        otra.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": llave_candado(op.id, tutor.telefono)})
        otra.commit()
    h.join(5)
    assert "Luis: $200.00" in respuestas[0]


def test_recarga_a_un_hijo_desvinculado_no_se_crea(db, tutor, hijos, escribir, tpv):
    escribir("hola")
    escribir("2")
    escribir("2")  # Ana
    escribir("100")
    tpv.tutores[tutor.tutor_ref]["alumnos"].remove(hijos[1])  # la administración la desvincula
    assert "ya no aparece" in escribir("1")
    assert db.query(Recarga).count() == 0


def test_si_el_anterior_tarda_demasiado_avisa_y_no_procesa(engine, op, tutor, hijos, escribir,
                                                           monkeypatch):
    from sqlalchemy import text

    from portal import bot
    monkeypatch.setattr(bot, "ESPERA_CANDADO", 0.3)
    escribir("hola")
    k = bot.llave_candado(op.id, tutor.telefono)
    with engine.connect() as otra:
        otra.execute(text("SELECT pg_advisory_lock(:k)"), {"k": k})
        assert escribir("1") == bot.MSG_OCUPADO
        otra.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": k})
        otra.commit()
    assert "Luis: $200.00" in escribir("1")


def test_mensaje_abandonado_se_atiende_al_repetirse(db, op, hijos, escribir):
    from portal.models import BotMensaje
    mid = str(uuid.uuid4())
    db.add(BotMensaje(operador_id=op.id, message_id=mid,
                      creado=datetime.now(timezone.utc) - timedelta(minutes=3)))
    db.commit()
    assert "¿Qué deseas hacer?" in escribir("hola", message_id=mid)


def test_mensaje_en_curso_no_se_atiende_dos_veces(db, op, hijos, c, conduit):
    from portal.models import BotMensaje
    from tests.test_conduit_entrante import MENSAJE, _firmado
    db.add(BotMensaje(operador_id=op.id, message_id=MENSAJE["message_id"]))  # sin respuesta aún
    db.commit()
    assert _firmado(c, "/message", MENSAJE).status_code == 200
    assert conduit.respuestas == []


def test_paso_y_respuesta_se_guardan_juntos(db, op, hijos, escribir):
    """El paso nuevo y la respuesta del mensaje quedan en la misma transacción."""
    from portal.models import BotConversacion, BotMensaje
    escribir("hola")
    escribir("7")  # tarjetas → elegir hijo
    paso_antes = db.query(BotConversacion).one().datos["paso"]
    mid = str(uuid.uuid4())
    escribir("1", message_id=mid)  # elige a Luis → «¿cuál tarjeta?»
    db.expire_all()
    m = db.query(BotMensaje).filter_by(message_id=mid).one()
    conv = db.query(BotConversacion).one()
    assert m.respuesta and conv.datos["paso"] == "tar_cred" != paso_antes


@pytest.mark.parametrize("numero,esperado", [
    ("+528341445807", "8341445807"), ("+5218341445807", "8341445807"),
    ("528341445807", "8341445807"),
    ("+18341445807", None), ("+448341445807", None), ("+4408341445807", None),
    ("8341445807", None), ("+52834144580", None), ("", None), ("+52８３４１４４５８０７", None)])
def test_solo_numeros_mexicanos(numero, esperado):
    from portal.bot import telefono10
    assert telefono10(numero) == esperado


def test_numero_extranjero_con_la_misma_terminacion_no_es_el_papa(hijos, escribir):
    assert "no está registrado" in escribir("hola", numero="+448341445807")
