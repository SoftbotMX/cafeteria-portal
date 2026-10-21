import re
from datetime import date, datetime, time, timezone

import pytest
from fastapi.testclient import TestClient

from portal.models import Bitacora, ConfigPedidos
from portal.tutor_tpv import MSG_SIN_TPV
from portal.reglas import ahora_utc
from tests.fabricas import crear_operador, crear_tutor, csrf_de, entrar_tutor
from tests.tpv_falso import TPVFalso

# miércoles 21/10/2026, 12:00 UTC = 06:00 en Ciudad de México
AHORA = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
MANANA = "2026-10-22"

MENU = [
    {"id": 10, "nombre": "Torta de jamón", "categoria": "Comida", "precio": 45},
    {"id": 11, "nombre": "Agua de limón", "categoria": "Bebidas", "precio": 15.5},
    {"id": 12, "nombre": "Quesadilla", "categoria": "Comida", "precio": 30},
]


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
    """Hora controlable: reloj['t'] = datetime aware en UTC."""
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


def _revisar(c, hijo, fecha=MANANA, **cants):
    pagina = c.get(f"/hijos/{hijo['id']}/pedir?fecha={fecha}")
    datos = {"csrf": csrf_de(pagina), "fecha": fecha}
    datos.update({f"cant_{k}": v for k, v in cants.items()})
    return c.post(f"/hijos/{hijo['id']}/pedir/revisar", data=datos)


def _ocultos(resp) -> dict:
    """Campos ocultos de la página de confirmación, tal como los enviaría el navegador."""
    campos: dict[str, list[str]] = {}
    for tag in re.findall(r"<input[^>]*>", resp.text):
        n = re.search(r'name="([^"]*)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if 'type="hidden"' in tag and n:
            campos.setdefault(n.group(1), []).append(v.group(1) if v else "")
    return campos


def _confirmar(c, hijo, ocultos):
    return c.post(f"/hijos/{hijo['id']}/pedir/confirmar", data=ocultos)


def _confirmacion(c, hijo, fecha=MANANA, **cants):
    r = _revisar(c, hijo, fecha, **cants)
    assert r.status_code == 200, r.text
    return _ocultos(r)


def test_menu_agrupado_por_categoria_con_precio(hijo, c):
    t = c.get(f"/hijos/{hijo['id']}/pedir").text
    assert t.index("Comida") < t.index("Torta de jamón") < t.index("Quesadilla") < t.index("Bebidas")
    assert t.index("Bebidas") < t.index("Agua de limón")
    assert t.count("Comida") == 1
    assert "$45.00" in t and "$15.50" in t and "$30.00" in t
    assert "mié 21/10/2026" in t  # primer día disponible (hoy)
    assert 'name="cant_10"' in t


def test_sin_dias_disponibles(hijo, c, db, op):
    db.add(ConfigPedidos(operador_id=op.id, dias_servicio="", dias_anticipacion=3))
    db.commit()
    t = c.get(f"/hijos/{hijo['id']}/pedir").text
    assert "No hay días disponibles para pedir" in t and "cant_10" not in t


def test_fecha_pedida_no_disponible_avisa(hijo, c, db, op):
    db.add(ConfigPedidos(operador_id=op.id, dias_servicio="01234", dias_anticipacion=14))
    db.commit()
    t = c.get(f"/hijos/{hijo['id']}/pedir?fecha=2026-10-24").text  # sábado
    assert "Ese día no hay servicio" in t and "cant_10" in t
    assert "Fecha no válida" in c.get(f"/hijos/{hijo['id']}/pedir?fecha=basura").text


def test_revisar_muestra_total_y_disponible(hijo, c):
    r = _revisar(c, hijo, **{"10": "2", "11": "1", "12": "0"})
    assert r.status_code == 200
    assert "no-store" in r.headers["cache-control"]
    t = r.text
    assert "2 × Torta de jamón" in t and "1 × Agua de limón" in t and "Quesadilla" not in t
    assert "$105.50" in t  # 2*45 + 15.50
    assert "$200.00" in t  # disponible de Luis
    assert "jue 22/10/2026" in t
    o = _ocultos(r)
    assert o["producto_id"] == ["10", "11"] and o["cantidad"] == ["2", "1"]
    assert o["fecha"] == [MANANA] and len(o["llave"]) == 1 and 8 <= len(o["llave"][0]) <= 120
    assert "csrf" in o


def test_revisar_aviso_si_no_alcanza(hijo, c, tpv):
    tpv.alumnos[hijo["id"]]["disponible"] = 20
    t = _revisar(c, hijo, **{"10": "1"}).text
    assert "No te alcanza el saldo de Luis Pérez" in t


def test_confirmar_crea_pedido_con_fecha(db, tutor, hijo, c, tpv):
    r = _confirmar(c, hijo, _confirmacion(c, hijo, **{"10": "2", "11": "1"}))
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert "PC-00001" in r.text and "jue 22/10/2026" in r.text
    assert "$105.50" in r.text and "$94.50" in r.text  # total del TPV y disponible nuevo
    assert tpv.llamadas["precompra"] == 1
    cuerpo = tpv.cuerpos_precompra[0]
    assert cuerpo["tutor_ref"] == tutor.tutor_ref and cuerpo["alumno_id"] == hijo["id"]
    assert cuerpo["fecha_entrega"] == MANANA
    assert cuerpo["items"] == [{"producto_id": 10, "cantidad": 2}, {"producto_id": 11, "cantidad": 1}]
    assert cuerpo["llave"]
    fila = db.query(Bitacora).filter_by(operacion="precompra").one()
    assert fila.actor == f"tutor:{tutor.id}" and fila.http_status == 200
    assert tpv.alumnos[hijo["id"]]["retenido"] == 105.5


def test_doble_confirmar_un_solo_pedido(hijo, c, tpv):
    o = _confirmacion(c, hijo, **{"10": "2"})
    r1 = _confirmar(c, hijo, o)
    r2 = _confirmar(c, hijo, o)
    assert r1.status_code == r2.status_code == 200
    assert "PC-00001" in r1.text and "PC-00001" in r2.text
    assert len(tpv.pedidos) == 1 and tpv.alumnos[hijo["id"]]["disponible"] == 110
    assert tpv.alumnos[hijo["id"]]["retenido"] == 90


def test_dos_revisiones_dos_llaves(hijo, c, tpv):
    a = _confirmacion(c, hijo, **{"10": "1"})
    b = _confirmacion(c, hijo, **{"10": "1"})
    assert a["llave"] != b["llave"]


@pytest.mark.parametrize("malo", ["-1", "1.5", "x", "21", "1e1", " ", "999999999999"])
def test_revisar_cantidades_invalidas(hijo, c, tpv, malo):
    r = _revisar(c, hijo, **{"10": malo})
    if malo.strip():
        assert r.status_code == 200 and "Cantidad no válida" in r.text
    assert "llave" not in _ocultos(r)
    assert tpv.llamadas.get("precompra", 0) == 0


def test_revisar_carrito_vacio_o_producto_fuera_del_menu(hijo, c, tpv):
    assert "al menos un producto" in _revisar(c, hijo, **{"10": "0"}).text
    assert "al menos un producto" in _revisar(c, hijo).text
    assert "no está en el menú" in _revisar(c, hijo, **{"999": "1"}).text
    assert tpv.llamadas.get("precompra", 0) == 0


@pytest.mark.parametrize("items", [
    ([], []), (["10"], ["0"]), (["10"], ["-1"]), (["10"], ["1.5"]), (["10"], ["x"]),
    (["10"], ["21"]), (["999"], ["1"]), (["10", "10"], ["1", "1"]), (["10", "11"], ["1"]),
    (["x"], ["1"]), (["10"], [""]),
])
def test_cantidades_invalidas_no_llaman_al_tpv(hijo, c, tpv, items):
    o = _confirmacion(c, hijo, **{"10": "1"})
    o["producto_id"], o["cantidad"] = items
    r = _confirmar(c, hijo, o)
    assert r.status_code == 200 and "precompra" not in tpv.llamadas
    assert "no-store" in r.headers["cache-control"]


def test_llave_invalida_no_llama_al_tpv(hijo, c, tpv):
    for llave in ("", "x" * 121, "con espacio"):
        o = _confirmacion(c, hijo, **{"10": "1"})
        o["llave"] = llave
        _confirmar(c, hijo, o)
    assert "precompra" not in tpv.llamadas


def test_fecha_tras_limite_rechazada_en_confirmar(db, op, hijo, c, tpv, reloj):
    db.add(ConfigPedidos(operador_id=op.id, hora_limite=time(9, 0), dias_servicio="0123456",
                         dias_anticipacion=14))
    db.commit()
    hoy = "2026-10-21"
    o = _confirmacion(c, hijo, fecha=hoy, **{"10": "1"})  # 06:00 local: todavía se puede
    reloj["t"] = datetime(2026, 10, 21, 15, 30, tzinfo=timezone.utc)  # 09:30 local
    r = _confirmar(c, hijo, o)
    assert "hora límite" in r.text and "09:00" in r.text
    assert "precompra" not in tpv.llamadas


def test_revisar_tras_limite_rechazada(db, op, hijo, c, tpv, reloj):
    db.add(ConfigPedidos(operador_id=op.id, hora_limite=time(9, 0), dias_servicio="0123456",
                         dias_anticipacion=14))
    db.commit()
    pagina = c.get(f"/hijos/{hijo['id']}/pedir")
    reloj["t"] = datetime(2026, 10, 21, 15, 30, tzinfo=timezone.utc)
    r = c.post(f"/hijos/{hijo['id']}/pedir/revisar",
               data={"csrf": csrf_de(pagina), "fecha": "2026-10-21", "cant_10": "1"})
    assert "hora límite" in r.text and "llave" not in _ocultos(r)


def test_fecha_sin_servicio_rechazada(db, op, hijo, c, tpv):
    db.add(ConfigPedidos(operador_id=op.id, dias_servicio="01234", dias_anticipacion=14))
    db.commit()
    o = _confirmacion(c, hijo, fecha="2026-10-23", **{"10": "1"})  # viernes: hay servicio
    o["fecha"] = ["2026-10-24"]  # sábado
    r = _confirmar(c, hijo, o)
    assert "Ese día no hay servicio" in r.text and "precompra" not in tpv.llamadas
    o["fecha"] = ["no-es-fecha"]
    assert "Fecha no válida" in _confirmar(c, hijo, o).text
    assert "precompra" not in tpv.llamadas


def test_saldo_insuficiente_mensaje_claro(db, hijo, c, tpv):
    tpv.alumnos[hijo["id"]]["disponible"] = 20
    o = _confirmacion(c, hijo, **{"10": "1"})
    r = _confirmar(c, hijo, o)
    assert "No te alcanza el saldo de Luis Pérez para este pedido." in r.text
    assert tpv.pedidos == [] and tpv.alumnos[hijo["id"]]["disponible"] == 20
    fila = db.query(Bitacora).filter_by(operacion="precompra").one()
    assert fila.http_status == 409


def test_tpv_caido_al_confirmar_permite_reintentar_con_la_misma_llave(hijo, c, tpv):
    o = _confirmacion(c, hijo, **{"10": "1"})
    tpv.fallar_en = {"precompra"}
    r = _confirmar(c, hijo, o)
    assert "No se pudo contactar al TPV" in r.text
    assert _ocultos(r)["llave"] == o["llave"]  # el reintento repite la misma llave
    tpv.fallar_en = set()
    r2 = _confirmar(c, hijo, _ocultos(r))
    assert "PC-00001" in r2.text and len(tpv.pedidos) == 1


def test_menu_caido(hijo, c, tpv):
    tpv.fallar_en = {"menu"}
    t = c.get(f"/hijos/{hijo['id']}/pedir").text
    assert "No pudimos consultar el menú" in t and "cant_" not in t


def test_alumno_ajeno_404_sin_precompra(db, op, tutor, hijo, c, tpv):
    ajeno = crear_tutor(db, op, telefono="8340000001")
    otro = tpv.agregar_hijo(ajeno.tutor_ref, "Ajeno", disponible=500)
    pagina = c.get(f"/hijos/{hijo['id']}/pedir")
    datos = {"csrf": csrf_de(pagina), "fecha": MANANA}
    assert c.get(f"/hijos/{otro['id']}/pedir").status_code == 404
    assert c.post(f"/hijos/{otro['id']}/pedir/revisar",
                  data={**datos, "cant_10": "1"}).status_code == 404
    assert c.post(f"/hijos/{otro['id']}/pedir/confirmar", data={
        **datos, "producto_id": ["10"], "cantidad": ["1"], "llave": "k" * 20}).status_code == 404
    assert c.post(f"/hijos/{otro['id']}/pedir/total", data=datos).status_code == 404
    assert "precompra" not in tpv.llamadas and tpv.alumnos[otro["id"]]["disponible"] == 500


def test_sin_csrf_403(hijo, c, tpv):
    o = _confirmacion(c, hijo, **{"10": "1"})
    o.pop("csrf")
    assert _confirmar(c, hijo, o).status_code == 403
    o["csrf"] = ["malo"]
    assert _confirmar(c, hijo, o).status_code == 403
    assert c.post(f"/hijos/{hijo['id']}/pedir/revisar",
                  data={"fecha": MANANA, "cant_10": "1"}).status_code == 403
    assert c.post(f"/hijos/{hijo['id']}/pedir/total", data={"fecha": MANANA}).status_code == 403
    assert "precompra" not in tpv.llamadas


def test_total_en_vivo(hijo, c):
    pagina = c.get(f"/hijos/{hijo['id']}/pedir")
    base = {"csrf": csrf_de(pagina), "fecha": MANANA, "cant_11": "x"}
    t = c.post(f"/hijos/{hijo['id']}/pedir/total", data={**base, "cant_10": "2"}).text
    assert "$90.00" in t and "<html" not in t and 'id="total"' in t  # 11 inválida = 0
    assert "$900.00" in c.post(f"/hijos/{hijo['id']}/pedir/total", data={**base, "cant_10": "20"}).text
    assert "$0.00" in c.post(f"/hijos/{hijo['id']}/pedir/total", data={**base, "cant_10": "21"}).text


def test_pedir_pide_sesion(op, c, tpv):
    assert c.get("/hijos/1/pedir").status_code == 303


def test_hijo_trae_boton_pedir(hijo, c):
    assert f'/hijos/{hijo["id"]}/pedir' in c.get(f"/hijos/{hijo['id']}").text


def test_pedido_con_producto_que_cambio_de_precio_muestra_el_total_del_tpv(hijo, c, tpv):
    o = _confirmacion(c, hijo, **{"10": "1"})
    tpv.productos[0]["precio"] = 50  # cambió entre la revisión y la confirmación
    r = _confirmar(c, hijo, o)
    assert "$50.00" in r.text and "$45.00" not in r.text


def test_lecturas_caidas_al_confirmar_conservan_la_llave(hijo, c, tpv):
    o = _confirmacion(c, hijo, **{"10": "1"})
    tpv.fallar_en = {"tutor/alumnos"}
    r = _confirmar(c, hijo, o)
    assert MSG_SIN_TPV in r.text and _ocultos(r)["llave"] == o["llave"]
    assert "precompra" not in tpv.llamadas
    tpv.fallar_en = set()
    assert "PC-00001" in _confirmar(c, hijo, _ocultos(r)).text
    assert len(tpv.pedidos) == 1


def test_total_en_vivo_con_tpv_caido_no_borra_el_carrito(hijo, c, tpv):
    pagina = c.get(f"/hijos/{hijo['id']}")  # sin tocar el menú: nada en caché
    tpv.fallar_en = {"menu"}
    r = c.post(f"/hijos/{hijo['id']}/pedir/total",
               data={"csrf": csrf_de(pagina), "fecha": MANANA, "cant_10": "1"})
    assert r.status_code == 503  # HTMX no intercambia respuestas de error


def test_hora_limite_se_mide_tras_las_lecturas_al_tpv(db, op, hijo, c, tpv, reloj):
    db.add(ConfigPedidos(operador_id=op.id, hora_limite=time(9, 0), dias_servicio="0123456",
                         dias_anticipacion=14))
    db.commit()
    reloj["t"] = datetime(2026, 10, 21, 14, 59, tzinfo=timezone.utc)  # 08:59 local
    o = _confirmacion(c, hijo, fecha="2026-10-21", **{"10": "1"})
    original = tpv._operar

    def lento(op_, d):
        reloj["t"] = datetime(2026, 10, 21, 15, 1, tzinfo=timezone.utc)  # las lecturas tardaron
        return original(op_, d)

    tpv._operar = lento
    r = _confirmar(c, hijo, o)
    assert "hora límite" in r.text and "precompra" not in tpv.llamadas


def test_rechazo_local_tras_intento_sin_respuesta_avisa_que_pudo_registrarse(db, op, hijo, c, tpv, reloj):
    db.add(ConfigPedidos(operador_id=op.id, hora_limite=time(9, 0), dias_servicio="0123456",
                         dias_anticipacion=14))
    db.commit()
    reloj["t"] = datetime(2026, 10, 21, 14, 59, tzinfo=timezone.utc)
    o = _confirmacion(c, hijo, fecha="2026-10-21", **{"10": "1"})
    tpv.fallar_en = {"precompra"}
    assert "No se pudo contactar" in _confirmar(c, hijo, o).text
    tpv.fallar_en = set()
    reloj["t"] = datetime(2026, 10, 21, 15, 5, tzinfo=timezone.utc)
    t = _confirmar(c, hijo, o).text
    assert "hora límite" in t and "puede haberse registrado" in t


def test_reenvio_de_pedido_cancelado_no_dice_que_quedo_apartado(hijo, c, tpv):
    o = _confirmacion(c, hijo, **{"10": "1"})
    _confirmar(c, hijo, o)
    tpv.pedidos[0]["estado"] = "cancelada"
    t = _confirmar(c, hijo, o).text
    assert "Cancelado" in t and "Quedó apartado" not in t


def test_respuesta_sin_pedido_no_es_exito(hijo, c, tpv):
    o = _confirmacion(c, hijo, **{"10": "1"})
    import httpx
    tpv._precompra = lambda d: httpx.Response(200, json={"ok": True})
    t = _confirmar(c, hijo, o).text
    assert "Quedó apartado" not in t and "respondió algo inesperado" in t


def test_total_en_vivo_no_pide_el_menu_en_cada_clic(hijo, c, tpv):
    pagina = c.get(f"/hijos/{hijo['id']}/pedir")
    base = {"csrf": csrf_de(pagina), "fecha": MANANA, "cant_10": "1"}
    for _ in range(5):
        assert c.post(f"/hijos/{hijo['id']}/pedir/total", data=base).status_code == 200
    assert tpv.llamadas["menu"] == 1
    # revisar y confirmar siempre leen el menú fresco
    _confirmar(c, hijo, _confirmacion(c, hijo, **{"10": "1"}))
    assert tpv.llamadas["menu"] == 3


def test_rechazo_del_tpv_tras_intento_sin_respuesta_tambien_avisa(hijo, c, tpv):
    o = _confirmacion(c, hijo, **{"10": "1"})
    tpv.fallar_en = {"precompra"}
    _confirmar(c, hijo, o)
    tpv.fallar_en = set()
    tpv.alumnos[hijo["id"]]["disponible"] = 1  # ahora el TPV lo rechaza por saldo
    t = _confirmar(c, hijo, o).text
    assert "No te alcanza el saldo" in t and "puede haberse registrado" in t


def test_respuesta_200_ilegible_cuenta_como_intento_incierto(db, op, hijo, c, tpv, reloj):
    import httpx
    db.add(ConfigPedidos(operador_id=op.id, hora_limite=time(9, 0), dias_servicio="0123456",
                         dias_anticipacion=14))
    db.commit()
    reloj["t"] = datetime(2026, 10, 21, 14, 59, tzinfo=timezone.utc)
    o = _confirmacion(c, hijo, fecha="2026-10-21", **{"10": "1"})
    buena = tpv._precompra
    tpv._precompra = lambda d: httpx.Response(200, content=b"no es json")
    assert "No se pudo contactar" in _confirmar(c, hijo, o).text
    tpv._precompra = buena
    reloj["t"] = datetime(2026, 10, 21, 15, 5, tzinfo=timezone.utc)
    assert "puede haberse registrado" in _confirmar(c, hijo, o).text
