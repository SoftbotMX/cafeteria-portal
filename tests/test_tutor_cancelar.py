from datetime import date, datetime, time, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.models import Bitacora, ConfigPedidos
from portal.reglas import ahora_utc
from tests.fabricas import crear_operador, crear_tutor, csrf_de, entrar_tutor, texto
from tests.test_tutor_pedir import MENU, _confirmacion, _confirmar, _ocultos
from tests.tpv_falso import TPVFalso

# miércoles 21/10/2026, 12:00 UTC = 06:00 en Ciudad de México
AHORA = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
HOY, MANANA = "2026-10-21", "2026-10-22"


@pytest.fixture
def op(db):
    return crear_operador(db)


@pytest.fixture
def tpv(app):
    f = TPVFalso(secreto_tutor="secreto-tutor")
    f.productos = [dict(p) for p in MENU]
    f.hoy = date(2026, 10, 21)
    app.state.tpv_transport = f.transport
    return f


@pytest.fixture
def reloj(app):
    estado = {"t": AHORA}
    app.dependency_overrides[ahora_utc] = lambda: estado["t"]
    yield estado
    app.dependency_overrides.pop(ahora_utc, None)


@pytest.fixture
def c(app):
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


@pytest.fixture
def tutor(db, op):
    return crear_tutor(db, op, debe_cambiar=False)


@pytest.fixture
def hijo(tutor, tpv, c, reloj):
    h = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", disponible=200)
    assert entrar_tutor(c, "8341445807", "temporal123").status_code == 303
    return h


def _pedir(c, hijo, fecha=MANANA, producto="10", cantidad="2"):
    r = _confirmar(c, hijo, _confirmacion(c, hijo, fecha=fecha, **{producto: cantidad}))
    assert r.status_code == 200 and "Pedido listo" in r.text, r.text


def _url(hijo, pedido_id=1):
    return f"/hijos/{hijo['id']}/pedidos/{pedido_id}/cancelar"


def _cancelar(c, hijo, pedido_id=1):
    """GET de confirmación + POST con los campos tal como los mandaría el navegador."""
    g = c.get(_url(hijo, pedido_id))
    assert g.status_code == 200, g.text
    return c.post(_url(hijo, pedido_id), data=_ocultos(g))


def _config_limite(db, op):
    db.add(ConfigPedidos(operador_id=op.id, hora_limite=time(9, 0), dias_servicio="0123456",
                         dias_anticipacion=14))
    db.commit()


def test_lista_pedidos_con_estados_legibles(hijo, c, tpv):
    _pedir(c, hijo, fecha=HOY, cantidad="1")       # 1: hoy
    _pedir(c, hijo, fecha="2026-10-25", cantidad="1")  # 2: más lejos
    _pedir(c, hijo, fecha=MANANA, cantidad="1")    # 3: mañana
    tpv.pedidos[0]["fecha_entrega"] = "2026-10-19"  # 1 pasó
    tpv.pedidos[0]["estado"] = "cobrada"
    t = c.get(f"/hijos/{hijo['id']}").text
    assert "Entregado" in t and "Por recoger" in t
    assert "PC-00001" in t and "PC-00002" in t and "PC-00003" in t
    # próximos primero y ascendentes por día; luego los demás
    assert t.index("PC-00003") < t.index("PC-00002") < t.index("PC-00001")
    assert "jue 22/10/2026" in t and "dom 25/10/2026" in t and "lun 19/10/2026" in t
    assert t.count("Cancelar") == 2  # sólo los dos retenidos


def test_pedidos_pasados_van_en_orden_descendente(hijo, c, tpv):
    for fecha in ("2026-10-16", "2026-10-19", "2026-10-20"):
        _pedir(c, hijo, fecha=MANANA, cantidad="1")
        tpv.pedidos[-1]["fecha_entrega"] = fecha
        tpv.pedidos[-1]["estado"] = "liberada"
    t = c.get(f"/hijos/{hijo['id']}").text
    assert t.index("PC-00003") < t.index("PC-00002") < t.index("PC-00001")
    assert "No se recogió" in t and "Cancelar" not in t


def test_sin_pedidos_lo_dice(hijo, c):
    assert "Todavía no hay pedidos" in c.get(f"/hijos/{hijo['id']}").text


def test_pedidos_caidos_no_tumban_la_pantalla(hijo, c, tpv):
    _pedir(c, hijo)
    tpv.fallar_en = {"alumno/pedidos"}
    t = texto(c.get(f"/hijos/{hijo['id']}").text)
    assert "Luis Pérez" in t and "$110.00" in t and "Saldo apartado" in t  # saldo y movimientos
    assert "No pudimos consultar los pedidos" in t and "PC-00001" not in t


def test_cancelar_pedido_futuro(db, tutor, hijo, c, tpv):
    _pedir(c, hijo)  # 2 × 45 = 90 -> disponible 110
    g = c.get(_url(hijo))
    assert g.status_code == 200 and "no-store" in g.headers["cache-control"]
    assert "PC-00001" in g.text and "2 × Torta de jamón" in g.text and "$90.00" in g.text
    assert "jue 22/10/2026" in g.text
    o = _ocultos(g)
    assert len(o["llave"]) == 1 and "csrf" in o
    assert "precompra/cancelar" not in tpv.llamadas  # el GET no escribe
    r = c.post(_url(hijo), data=o)
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert "Pedido PC-00001 cancelado" in r.text and "$200.00" in r.text
    assert tpv.cuerpos_cancelar == [{"pedido_id": 1, "llave": o["llave"][0],
                                     "tutor_ref": tutor.tutor_ref}]
    assert tpv.pedidos[0]["estado"] == "cancelada"
    assert tpv.alumnos[hijo["id"]]["disponible"] == 200 and tpv.alumnos[hijo["id"]]["retenido"] == 0
    fila = db.query(Bitacora).filter_by(operacion="precompra/cancelar").one()
    assert fila.actor == f"tutor:{tutor.id}" and fila.http_status == 200
    assert "Cancelado" in c.get(f"/hijos/{hijo['id']}").text


def test_cancelar_hoy_tras_limite_rechazado_sin_llamar(db, op, hijo, c, tpv, reloj):
    _config_limite(db, op)
    _pedir(c, hijo, fecha=HOY)  # 06:00 local: todavía se puede
    g = c.get(_url(hijo))
    assert "Cancelar pedido" in g.text
    o = _ocultos(g)
    reloj["t"] = datetime(2026, 10, 21, 15, 30, tzinfo=timezone.utc)  # 09:30 local
    r = c.post(_url(hijo), data=o)
    assert "hora límite" in r.text and "09:00" in r.text
    assert "precompra/cancelar" not in tpv.llamadas
    assert tpv.pedidos[0]["estado"] == "retenida"
    # y la confirmación ya no se ofrece, ni el botón
    assert "Cancelar pedido" not in c.get(_url(hijo)).text
    assert "Cancelar" not in c.get(f"/hijos/{hijo['id']}").text


def test_hora_limite_se_mide_tras_las_lecturas(db, op, hijo, c, tpv, reloj):
    _config_limite(db, op)
    reloj["t"] = datetime(2026, 10, 21, 14, 59, tzinfo=timezone.utc)  # 08:59
    _pedir(c, hijo, fecha=HOY)
    o = _ocultos(c.get(_url(hijo)))
    original = tpv._operar

    def lento(op_, d):
        reloj["t"] = datetime(2026, 10, 21, 15, 1, tzinfo=timezone.utc)  # las lecturas tardaron
        return original(op_, d)

    tpv._operar = lento
    r = c.post(_url(hijo), data=o)
    assert "hora límite" in r.text and "precompra/cancelar" not in tpv.llamadas


def test_pedido_de_otro_dia_futuro_se_cancela_tras_limite(db, op, hijo, c, tpv, reloj):
    _config_limite(db, op)
    _pedir(c, hijo, fecha=MANANA)
    reloj["t"] = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)
    assert "Pedido PC-00001 cancelado" in _cancelar(c, hijo).text


def test_sin_boton_para_cobrado_o_pasado(hijo, c, tpv):
    _pedir(c, hijo, fecha=HOY)
    _pedir(c, hijo, fecha=MANANA)
    tpv.pedidos[1]["estado"] = "cobrada"
    tpv.pedidos[0]["fecha_entrega"] = "2026-10-20"  # retenida pero de ayer
    t = c.get(f"/hijos/{hijo['id']}").text
    assert "PC-00001" in t and "PC-00002" in t and "Cancelar" not in t
    # y las confirmaciones tampoco se ofrecen, ni llaman
    for pid in (1, 2):
        g = c.get(_url(hijo, pid))
        assert "Cancelar pedido" not in g.text and "llave" not in _ocultos(g)
    assert "precompra/cancelar" not in tpv.llamadas


def test_pedido_ya_cancelado_no_se_ofrece(hijo, c, tpv):
    _pedir(c, hijo)
    tpv.pedidos[0]["estado"] = "cancelada"
    g = c.get(_url(hijo))
    assert "ya estaba cancelado" in g.text and "llave" not in _ocultos(g)


def test_pedido_de_otro_hijo_404(db, op, tutor, hijo, c, tpv):
    hermano = tpv.agregar_hijo(tutor.tutor_ref, "Sofía Pérez", disponible=100)
    _pedir(c, hijo)
    o = _ocultos(c.get(_url(hijo)))
    assert c.get(_url(hermano, 1)).status_code == 404  # existe, pero es de Luis
    assert c.post(_url(hermano, 1), data=o).status_code == 404
    assert c.get(_url(hijo, 999)).status_code == 404
    assert c.post(_url(hijo, 999), data=o).status_code == 404
    ajeno = crear_tutor(db, op, telefono="8340000001")
    otro = tpv.agregar_hijo(ajeno.tutor_ref, "Ajeno", disponible=500)
    assert c.get(_url(otro, 1)).status_code == 404
    assert c.post(_url(otro, 1), data=o).status_code == 404
    assert "precompra/cancelar" not in tpv.llamadas and tpv.pedidos[0]["estado"] == "retenida"


def test_doble_cancelar_una_sola_llamada_efectiva(hijo, c, tpv, db, op, reloj):
    _config_limite(db, op)
    _pedir(c, hijo, fecha=HOY)
    o = _ocultos(c.get(_url(hijo)))
    r1 = c.post(_url(hijo), data=o)
    # el segundo toque llega ya pasada la hora límite y con el pedido cancelado: ve el resultado
    reloj["t"] = datetime(2026, 10, 21, 15, 30, tzinfo=timezone.utc)
    r2 = c.post(_url(hijo), data=o)
    assert "Pedido PC-00001 cancelado" in r1.text and "Pedido PC-00001 cancelado" in r2.text
    assert tpv.alumnos[hijo["id"]]["disponible"] == 200 and tpv.alumnos[hijo["id"]]["retenido"] == 0
    liberaciones = [m for m in tpv.movimientos[hijo["id"]] if m["tipo"] == "liberacion"]
    assert len(liberaciones) == 1


def test_sin_csrf_403(hijo, c, tpv):
    _pedir(c, hijo)
    o = _ocultos(c.get(_url(hijo)))
    o.pop("csrf")
    assert c.post(_url(hijo), data=o).status_code == 403
    o["csrf"] = ["malo"]
    assert c.post(_url(hijo), data=o).status_code == 403
    assert "precompra/cancelar" not in tpv.llamadas


def test_llave_invalida_no_llama(hijo, c, tpv):
    _pedir(c, hijo)
    for llave in ("", "x" * 121, "con espacio"):
        o = _ocultos(c.get(_url(hijo)))
        o["llave"] = llave
        r = c.post(_url(hijo), data=o)
        assert "no es válida" in r.text
    assert "precompra/cancelar" not in tpv.llamadas


def test_tpv_caido_al_cancelar_reintenta_con_la_misma_llave(hijo, c, tpv):
    _pedir(c, hijo)
    o = _ocultos(c.get(_url(hijo)))
    tpv.fallar_en = {"precompra/cancelar"}
    r = c.post(_url(hijo), data=o)
    assert "No se pudo contactar al TPV" in r.text and _ocultos(r)["llave"] == o["llave"]
    tpv.fallar_en = set()
    r2 = c.post(_url(hijo), data=_ocultos(r))
    assert "Pedido PC-00001 cancelado" in r2.text
    assert tpv.llamadas["precompra/cancelar"] == 2 and tpv.alumnos[hijo["id"]]["disponible"] == 200


def test_lecturas_caidas_al_cancelar_conservan_la_llave(hijo, c, tpv):
    _pedir(c, hijo)
    o = _ocultos(c.get(_url(hijo)))
    tpv.fallar_en = {"alumno/pedidos"}
    r = c.post(_url(hijo), data=o)
    assert "No pudimos consultar" in r.text and _ocultos(r)["llave"] == o["llave"]
    # la página de reintento muestra el folio, no el id interno del pedido
    assert "Pedido PC-00001" in r.text and _ocultos(r)["folio"] == ["PC-00001"]
    assert "precompra/cancelar" not in tpv.llamadas
    tpv.fallar_en = set()
    assert "Pedido PC-00001 cancelado" in c.post(_url(hijo), data=_ocultos(r)).text


def test_confirmacion_sin_lecturas_dice_que_no_pudo(hijo, c, tpv):
    _pedir(c, hijo)
    tpv.fallar_en = {"alumno/pedidos"}
    g = c.get(_url(hijo))
    assert "No pudimos consultar" in g.text and "llave" not in _ocultos(g)


def test_rechazo_final_del_tpv_se_muestra_con_su_mensaje(db, hijo, c, tpv):
    _pedir(c, hijo)
    o = _ocultos(c.get(_url(hijo)))
    tpv._cancelar = lambda d: httpx.Response(409, json={"ok": False, "error": {
        "codigo": "rechazado", "mensaje": "El pedido PC-00001 ya no se puede cancelar."}})
    r = c.post(_url(hijo), data=o)
    assert "ya no se puede cancelar" in r.text and "cancelado</h1>" not in r.text
    assert "rechazó la operación" not in r.text and o["llave"][0] not in r.text


def test_rechazo_tras_intento_sin_respuesta_avisa_que_pudo_cancelarse(hijo, c, tpv):
    _pedir(c, hijo)
    o = _ocultos(c.get(_url(hijo)))
    tpv.fallar_en = {"precompra/cancelar"}
    c.post(_url(hijo), data=o)
    tpv.fallar_en = set()
    tpv._cancelar = lambda d: httpx.Response(409, json={"ok": False, "error": {
        "codigo": "rechazado", "mensaje": "El pedido PC-00001 ya no se puede cancelar."}})
    assert "puede haberse registrado" in c.post(_url(hijo), data=o).text


def test_respuesta_sin_pedido_cancelado_no_es_exito(hijo, c, tpv):
    _pedir(c, hijo)
    o = _ocultos(c.get(_url(hijo)))
    tpv._cancelar = lambda d: httpx.Response(200, json={"ok": True})
    t = c.post(_url(hijo), data=o).text
    assert "cancelado</h1>" not in t and "respondió algo inesperado" in t


def test_cancelar_pide_sesion(op, c, tpv):
    assert c.get("/hijos/1/pedidos/1/cancelar").status_code == 303
    assert c.post("/hijos/1/pedidos/1/cancelar", data={}).status_code == 303


def test_cancelacion_cuya_respuesta_se_perdio_se_confirma_al_repetir(hijo, c, tpv):
    _pedir(c, hijo)
    o = _ocultos(c.get(_url(hijo)))
    buena = tpv._cancelar

    def se_pierde(d):
        buena(d)  # el TPV sí cancela...
        return httpx.Response(503, json={"ok": False, "error": {"codigo": "x", "mensaje": "x"}})

    tpv._cancelar = se_pierde
    r = c.post(_url(hijo), data=o)
    assert "No se pudo contactar" in r.text and tpv.pedidos[0]["estado"] == "cancelada"
    tpv._cancelar = buena
    # ...y al repetir con la misma llave, el pedido ya cancelado no es «ya estaba cancelado»:
    # el TPV devuelve su respuesta guardada y el tutor ve el éxito.
    t = c.post(_url(hijo), data=_ocultos(r)).text
    assert "Pedido PC-00001 cancelado" in t and "$200.00" in t
    assert len([m for m in tpv.movimientos[hijo["id"]] if m["tipo"] == "liberacion"]) == 1


def test_pedido_cancelado_por_otro_lado_no_se_vuelve_a_cancelar(hijo, c, tpv):
    _pedir(c, hijo)
    o = _ocultos(c.get(_url(hijo)))
    tpv.pedidos[0]["estado"] = "cancelada"  # el admin lo canceló entre la confirmación y el toque
    assert "ya estaba cancelado" in c.post(_url(hijo), data=o).text
    assert "precompra/cancelar" not in tpv.llamadas


def test_repetir_la_cancelacion_muestra_el_saldo_de_hoy_no_el_guardado(hijo, c, tpv):
    _pedir(c, hijo)
    o = _ocultos(c.get(_url(hijo)))
    assert "$200.00" in c.post(_url(hijo), data=o).text
    tpv.alumnos[hijo["id"]]["disponible"] = 140  # gastó $60 después
    t = c.post(_url(hijo), data=o).text
    assert "Pedido PC-00001 cancelado" in t and "$140.00" in t and "$200.00" not in t
