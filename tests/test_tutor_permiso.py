from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.models import Bitacora
from portal.reglas import ahora_utc
from tests.fabricas import crear_operador, crear_tutor, entrar_tutor
from tests.test_tutor_pedir import _ocultos
from tests.tpv_falso import TPVFalso

AHORA = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
SI_TXT = "Luis Pérez podrá comprar sin pedido"
NO_TXT = "Luis Pérez sólo podrá recoger lo que pidas"


@pytest.fixture
def op(db):
    return crear_operador(db)


@pytest.fixture
def tpv(app):
    f = TPVFalso(secreto_tutor="secreto-tutor")
    app.state.tpv_transport = f.transport
    return f


@pytest.fixture(autouse=True)
def reloj(app):
    app.dependency_overrides[ahora_utc] = lambda: AHORA
    yield
    app.dependency_overrides.pop(ahora_utc, None)


@pytest.fixture
def c(app):
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


@pytest.fixture
def tutor(db, op):
    return crear_tutor(db, op, debe_cambiar=False)


@pytest.fixture
def hijo(tutor, tpv, c):
    h = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", disponible=200)
    assert entrar_tutor(c, "8341445807", "temporal123").status_code == 303
    return h


def _url(hijo):
    return f"/hijos/{hijo['id']}/permiso"


def _cambiar(c, hijo, valor):
    g = c.get(_url(hijo), params={"valor": valor})
    assert g.status_code == 200, g.text
    return c.post(_url(hijo), data=_ocultos(g))


def test_cambiar_permiso_a_no_y_a_si(db, tutor, hijo, c, tpv):
    g = c.get(_url(hijo), params={"valor": "no"})
    assert g.status_code == 200 and "no-store" in g.headers["cache-control"]
    assert NO_TXT in g.text and SI_TXT not in g.text
    o = _ocultos(g)
    assert o["valor"] == ["no"] and len(o["llave"]) == 1 and "csrf" in o
    assert "alumno/permiso" not in tpv.llamadas  # el GET no escribe
    r = c.post(_url(hijo), data=o)
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert NO_TXT in r.text and "listo" in r.text
    # booleano real, no la cadena "no"
    assert tpv.cuerpos_permiso == [{"alumno_id": hijo["id"], "compra_sin_pedido": False,
                                    "llave": o["llave"][0], "tutor_ref": tutor.tutor_ref}]
    assert tpv.alumnos[hijo["id"]]["compra_sin_pedido"] is False
    fila = db.query(Bitacora).filter_by(operacion="alumno/permiso").one()
    assert fila.actor == f"tutor:{tutor.id}" and fila.http_status == 200
    # la ficha lo refleja y ofrece el botón contrario
    t = c.get(f"/hijos/{hijo['id']}").text
    assert "sólo recoge lo que pide" in t and f"{_url(hijo)}?valor=si" in t
    r2 = _cambiar(c, hijo, "si")
    assert SI_TXT in r2.text
    assert tpv.cuerpos_permiso[-1]["compra_sin_pedido"] is True
    assert tpv.alumnos[hijo["id"]]["compra_sin_pedido"] is True
    t = c.get(f"/hijos/{hijo['id']}").text
    assert "puede comprar sin pedido" in t and f"{_url(hijo)}?valor=no" in t


def test_valor_invalido_400_sin_llamar(hijo, c, tpv):
    for v in ("", "tal vez", "true", "1", "SI"):
        g = c.get(_url(hijo), params={"valor": v})
        assert g.status_code == 400 and "Valor no válido" in g.text
    assert c.get(_url(hijo)).status_code == 400  # sin parámetro
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    for v in ("", "false", "0", "tal vez"):
        o["valor"] = [v]
        assert c.post(_url(hijo), data=o).status_code == 400
    assert "alumno/permiso" not in tpv.llamadas


def test_alumno_ajeno_404(db, op, tutor, hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    ajeno = crear_tutor(db, op, telefono="8340000001")
    otro = tpv.agregar_hijo(ajeno.tutor_ref, "Ajeno", disponible=500)
    assert c.get(_url(otro), params={"valor": "no"}).status_code == 404
    assert c.post(_url(otro), data=o).status_code == 404
    assert c.get("/hijos/999/permiso", params={"valor": "no"}).status_code == 404
    # el 404 va antes que el 400: no se revela nada de alumnos ajenos
    assert c.get(_url(otro), params={"valor": "x"}).status_code == 404
    assert "alumno/permiso" not in tpv.llamadas and tpv.alumnos[otro["id"]]["compra_sin_pedido"] is True


def test_sin_csrf_403(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    o.pop("csrf")
    assert c.post(_url(hijo), data=o).status_code == 403
    o["csrf"] = ["malo"]
    assert c.post(_url(hijo), data=o).status_code == 403
    assert "alumno/permiso" not in tpv.llamadas


def test_pide_sesion(op, c, tpv):
    assert c.get("/hijos/1/permiso?valor=no").status_code == 303
    assert c.post("/hijos/1/permiso", data={}).status_code == 303


def test_pedir_lo_que_ya_tiene_dice_ya_estaba_asi_sin_escribir(hijo, c, tpv):
    # el hijo ya puede comprar sin pedido: pedir «si» no cambia nada
    g = c.get(_url(hijo), params={"valor": "si"})
    assert "Ya estaba así" in g.text and "llave" not in _ocultos(g)
    # y si cambió entre la confirmación y el toque
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    tpv.alumnos[hijo["id"]]["compra_sin_pedido"] = False  # el otro tutor ya lo quitó
    r = c.post(_url(hijo), data=o)
    assert "Ya estaba así" in r.text and NO_TXT in r.text
    assert "alumno/permiso" not in tpv.llamadas


def test_doble_toque_ve_el_resultado_no_ya_estaba_asi(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    r1 = c.post(_url(hijo), data=o)
    r2 = c.post(_url(hijo), data=o)  # la ficha ya dice «no», pero esta llave ya entró
    assert NO_TXT in r1.text and NO_TXT in r2.text
    assert "Ya estaba así" not in r1.text and "Ya estaba así" not in r2.text
    assert tpv.llamadas["alumno/permiso"] == 2 and len(tpv.una_vez) == 1  # el TPV repite lo guardado


def test_tpv_caido_reintenta_con_la_misma_llave(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    tpv.fallar_en = {"alumno/permiso"}
    r = c.post(_url(hijo), data=o)
    assert "No se pudo contactar al TPV" in r.text and _ocultos(r)["llave"] == o["llave"]
    assert _ocultos(r)["valor"] == ["no"]
    tpv.fallar_en = set()
    assert NO_TXT in c.post(_url(hijo), data=_ocultos(r)).text
    assert tpv.alumnos[hijo["id"]]["compra_sin_pedido"] is False


def test_respuesta_perdida_se_confirma_al_repetir(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    buena = tpv._permiso

    def se_pierde(d):
        buena(d)  # el TPV sí lo cambia...
        return httpx.Response(503, json={"ok": False, "error": {"codigo": "x", "mensaje": "x"}})

    tpv._permiso = se_pierde
    r = c.post(_url(hijo), data=o)
    assert "No se pudo contactar" in r.text and tpv.alumnos[hijo["id"]]["compra_sin_pedido"] is False
    tpv._permiso = buena
    t = c.post(_url(hijo), data=_ocultos(r)).text  # ...y al repetir no es «ya estaba así»
    assert NO_TXT in t and "Ya estaba así" not in t


def test_llave_invalida_no_llama(hijo, c, tpv):
    for llave in ("", "x" * 121, "con espacio"):
        o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
        o["llave"] = [llave]
        assert "no es válida" in c.post(_url(hijo), data=o).text
    assert "alumno/permiso" not in tpv.llamadas


def test_lecturas_caidas(hijo, c, tpv):
    tpv.fallar_en = {"tutor/alumnos"}
    g = c.get(_url(hijo), params={"valor": "no"})
    assert "No pudimos consultar" in g.text and "llave" not in _ocultos(g)


def test_llave_reutilizada_tiene_mensaje_propio(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    tpv._permiso = lambda d: httpx.Response(409, json={"ok": False, "error": {
        "codigo": "llave_reutilizada", "mensaje": "Esa llave ya se usó para otra operación."}})
    t = c.post(_url(hijo), data=o).text
    assert "vuelve a armarlo" not in t and "pedido" not in t.lower().replace("sin pedido", "")
    assert "Vuelve a abrir" in t and o["llave"][0] not in t


def test_rechazo_final_se_muestra(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    tpv._permiso = lambda d: httpx.Response(404, json={"ok": False, "error": {
        "codigo": "alumno_desconocido", "mensaje": "Alumno desconocido."}})
    t = c.post(_url(hijo), data=o).text
    assert "Alumno desconocido." in t and "listo" not in t


def test_respuesta_con_otro_valor_no_es_exito(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    tpv._permiso = lambda d: httpx.Response(200, json={"ok": True, "alumno": {"compra_sin_pedido": True}})
    t = c.post(_url(hijo), data=o).text
    assert "respondió algo inesperado" in t and NO_TXT not in t


def test_rechazo_tras_intento_sin_respuesta_avisa(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    tpv.fallar_en = {"alumno/permiso"}
    c.post(_url(hijo), data=o)
    tpv.fallar_en = set()
    tpv._permiso = lambda d: httpx.Response(404, json={"ok": False, "error": {
        "codigo": "alumno_desconocido", "mensaje": "Alumno desconocido."}})
    assert "puede haberse registrado" in c.post(_url(hijo), data=o).text


def test_repetir_cuando_ya_se_cambio_otra_vez_no_miente(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    assert NO_TXT in c.post(_url(hijo), data=o).text
    tpv.alumnos[hijo["id"]]["compra_sin_pedido"] = True  # el otro tutor lo volvió a permitir
    t = c.post(_url(hijo), data=o).text  # doble toque tardío con la llave vieja
    assert "otro tutor lo haya cambiado" in t and NO_TXT not in t
    assert tpv.alumnos[hijo["id"]]["compra_sin_pedido"] is True  # el TPV no lo deshizo


def test_reintento_tras_respuesta_perdida_y_cambio_ajeno_no_miente(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo), params={"valor": "no"}))
    buena = tpv._permiso

    def se_pierde(d):
        buena(d)
        return httpx.Response(503, json={"ok": False, "error": {"codigo": "x", "mensaje": "x"}})

    tpv._permiso = se_pierde
    r = c.post(_url(hijo), data=o)
    tpv._permiso = buena
    tpv.alumnos[hijo["id"]]["compra_sin_pedido"] = True  # otro tutor lo revirtió
    t = c.post(_url(hijo), data=_ocultos(r)).text
    assert "otro tutor lo haya cambiado" in t and NO_TXT not in t
    assert tpv.alumnos[hijo["id"]]["compra_sin_pedido"] is True
