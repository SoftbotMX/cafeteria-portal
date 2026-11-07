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


def test_recargar_total_y_repartir_entre_hijos(db, hijos, escribir, mp):
    escribir("hola")
    r = escribir("2")
    assert "¿Cuánto vas a recargar en total?" in r and "¿De quién?" not in r
    r = escribir("300")
    assert "Te quedan $300.00" in r and "Luis" in r
    assert "entre 0 y 300" in escribir("400")
    r = escribir("$200")
    assert "*Total a pagar: $300.00*" in r and "Luis: $200.00" in r and "Ana: $100.00" in r  # a Ana, el resto
    assert "1 Confirmar" in r
    r = escribir("1")
    assert r.startswith("*Paga $300.00 aquí:*\nhttps://mp.test/checkout")
    assert "*La liga vence en 30 minutos.*" in r and "*Conversación terminada" in r
    assert "¿Qué deseas hacer?" in escribir("hola")  # tras la liga, empieza de nuevo
    rec = db.query(Recarga).one()
    assert rec.total == 300 and {p["nombre"]: p["importe"] for p in rec.reparto} == {
        "Luis": 200, "Ana": 100}


def test_recargar_todo_a_un_hijo_no_pregunta_por_los_demas(db, hijos, escribir, mp):
    escribir("hola")
    escribir("2")
    escribir("1,000")
    r = escribir("1000")
    assert "Luis: $1,000.00" in r and "Ana" not in r and "Total a pagar: $1,000.00" in r


def test_recargar_respeta_el_minimo_y_cancelar_no_crea_nada(db, hijos, escribir):
    escribir("hola")
    escribir("2")
    assert "$50" in escribir("30")
    assert "$2,000" in escribir("2001")
    escribir("50")
    assert "Total a pagar: $50.00" in escribir("0")  # todo a Ana
    assert "no se hizo nada" in escribir("2")
    assert db.query(Recarga).count() == 0


def test_recargar_con_un_solo_hijo_pregunta_directo(db, tutor, tpv, escribir):
    tpv.agregar_hijo(tutor.tutor_ref, "Luis", disponible=0)
    escribir("hola")
    r = escribir("2")
    assert "¿Cuánto le recargas a Luis?" in r


def _pedir_torta(escribir, dia="2"):
    escribir("hola")
    escribir("3")
    r = escribir("1")  # Luis
    assert "1 Hoy" in r and "2 Mañana" in r
    r = escribir(dia)
    assert "1 Almuerzo (1)" in r and "2 Bebidas (1)" in r  # por categoría
    assert "1 Torta $45.00" in escribir("1")
    assert "1) Torta × 2" in escribir("1 2")
    r = escribir("categorías")
    assert "Llevas:" in r and "1) Torta × 2" in r
    assert "1 Agua $15.00" in escribir("2")
    assert "2) Agua × 1" in escribir("1")
    r = escribir("listo")
    assert "Total: $105.00" in r
    return escribir("1")


def _a_almuerzo(escribir):
    escribir("hola")
    escribir("3")
    escribir("1")
    escribir("2")
    return escribir("1")  # Almuerzo


def test_instrucciones_de_productos_cortas_con_tip(hijos, escribir):
    r = _a_almuerzo(escribir)
    assert "repítelo para sumar otro igual" in r and "«2 3» agrega 3 del producto 2" in r
    assert "Un producto por mensaje" not in r


def test_repetir_la_seleccion_suma(hijos, escribir):
    _a_almuerzo(escribir)
    assert "1) Torta × 1" in escribir("1")
    assert "1) Torta × 2" in escribir("1")
    r = escribir("1 3")
    assert "1) Torta × 5" in r and "Total: $225.00" in r
    assert "«quitar 1»" in r and "«listo»" in r and "«categorías»" in r


def test_repetir_no_pasa_del_maximo(hijos, escribir):
    from portal.bot import MAX_POR_LINEA
    _a_almuerzo(escribir)
    escribir(f"1 {MAX_POR_LINEA}")
    r = escribir("1")
    assert f"Máximo {MAX_POR_LINEA}" in r
    assert f"1) Torta × {MAX_POR_LINEA}" in escribir("categorías")


def test_quitar_un_producto_del_carrito(hijos, escribir):
    _a_almuerzo(escribir)
    escribir("1")
    escribir("categorías")
    escribir("2")  # Bebidas
    assert "2) Agua × 1" in escribir("1")
    r = escribir("quitar 1")  # la línea 1 del carrito: la torta (una sola: sin preguntar)
    assert "Quité Torta" in r and "1) Agua × 1" in r and "Torta ×" not in r
    assert "No tienes un producto 5" in escribir("quitar 5")


def test_quitar_con_varias_pregunta_todas_o_algunas(hijos, escribir):
    _a_almuerzo(escribir)
    escribir("1 5")
    r = escribir("quitar 1")
    assert "Llevas 5 × Torta" in r and "1 Quitar todas" in r and "2 Quitar algunas" in r
    assert "No entendí" in escribir("7")
    assert "del 1 al 4" in escribir("2")
    assert "del 1 al 4" in escribir("5")
    r = escribir("3")
    assert "Quité 3 × Torta" in r and "1) Torta × 2" in r and "Agrega otro" in r
    r = escribir("quitar 1")
    assert "Llevas 2 × Torta" in r
    r = escribir("2")  # con dos, «algunas» sólo puede ser una: no pregunta cuántas
    assert "Quité 1 × Torta" in r and "1) Torta × 1" in r


def test_quitar_todas_desde_categorias_regresa_a_categorias(hijos, escribir):
    _a_almuerzo(escribir)
    escribir("1 3")
    escribir("categorías")
    assert "Quitar todas" in escribir("quitar 1")
    r = escribir("1")
    assert "Quité Torta" in r and "¿Qué quieres ver?" in r and "Llevas:" not in r


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
    escribir("100")
    escribir("0")  # nada a Luis: todo a Ana
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


def test_bot_si_cambia_el_precio_no_pide(hijos, escribir, tpv):
    escribir("hola")
    escribir("3")
    escribir("1")
    escribir("2")
    escribir("1")  # Almuerzo
    escribir("1 2")
    escribir("listo")
    tpv.productos[0]["precio"] = 50
    assert "precio" in escribir("1")
    assert tpv.cuerpos_precompra == []


def test_bot_reintento_de_recarga_revalida_hijos(db, tutor, hijos, escribir, mp, tpv):
    escribir("hola")
    escribir("2")
    escribir("100")
    escribir("0")  # nada a Luis: todo a Ana
    mp.fallar.add("preferencia")
    assert "reintentar" in escribir("1")
    mp.fallar.clear()
    tpv.tutores[tutor.tutor_ref]["alumnos"].remove(hijos[1])
    assert "ya no aparece" in escribir("1")
    assert db.query(Recarga).one().init_point is None


def test_confirmacion_guardada_sin_total_sigue_funcionando(db, hijos, escribir, tpv):
    from portal.models import BotConversacion
    escribir("hola")
    escribir("3")
    escribir("1")
    escribir("2")
    escribir("1")  # Almuerzo
    escribir("1 2")
    escribir("listo")
    conv = db.query(BotConversacion).one()
    conv.datos = {k: v for k, v in conv.datos.items() if k != "total"}  # formato anterior
    db.commit()
    assert "¡Listo!" in escribir("1")



# -- categorías y terminar (pedido del usuario, 2026-11-07) ----------------------------------------
def test_una_sola_categoria_va_directo_a_los_productos(hijos, escribir, tpv):
    tpv.productos = [{"id": 10, "nombre": "Torta", "categoria": "Almuerzo", "precio": 45}]
    escribir("hola")
    escribir("3")
    escribir("1")
    r = escribir("2")
    assert "Almuerzo:" in r and "1 Torta $45.00" in r and "categorías" not in r


def test_listo_desde_la_lista_de_categorias(hijos, escribir):
    escribir("hola")
    escribir("3")
    escribir("1")
    escribir("2")
    assert "Todavía no agregas nada" in escribir("listo")
    escribir("2")  # Bebidas
    escribir("1 3")
    r = escribir("categorias")
    assert "«listo» lo termina" in r
    assert "Total: $45.00" in escribir("listo")


def test_categoria_inexistente(hijos, escribir):
    escribir("hola")
    escribir("3")
    escribir("1")
    escribir("2")
    assert "No entendí" in escribir("9")


def test_terminar_desde_el_menu(hijos, escribir):
    escribir("hola")
    assert "8 Terminar" in escribir("menu")
    assert "hasta pronto" in escribir("8")
    assert "Hola Ana." in escribir("quiero pedir")


def test_despedirse_en_cualquier_paso(hijos, escribir, tpv):
    escribir("hola")
    escribir("3")
    escribir("1")
    assert "hasta pronto" in escribir("Gracias")
    assert "Hola Ana." in escribir("1")
    assert tpv.cuerpos_precompra == []


def test_cero_vuelve_al_menu_fuera_del_reparto(hijos, escribir):
    escribir("hola")
    escribir("3")  # Pedir: ¿De quién?
    assert "¿Qué deseas hacer?" in escribir("0")
    escribir("2")
    escribir("300")
    r = escribir("0")  # en el reparto, «0» es nada para Luis
    assert "Ana: $300.00" in r and "¿Qué deseas hacer?" not in r


def test_desvincular_a_mitad_del_reparto_no_recorre_montos(db, tutor, hijos, escribir, tpv):
    escribir("hola")
    escribir("2")
    escribir("300")  # pregunta por Luis
    tpv.tutores[tutor.tutor_ref]["alumnos"].remove(hijos[0])
    r = escribir("100")
    assert "ya no aparece" in r and "Recarga:" not in r
    assert db.query(Recarga).count() == 0


def test_todos_por_igual_de_una_conversacion_anterior_lleva_al_reparto(db, tutor, hijos, escribir):
    escribir("hola")
    c = db.query(BotConversacion).one()
    c.datos = {"accion": "recargar", "paso": "hijo", "opciones": [h["id"] for h in hijos]}
    db.commit()
    assert "¿Cuánto vas a recargar en total?" in escribir("3")
