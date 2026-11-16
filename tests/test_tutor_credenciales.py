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
BLOQUEAR_TARJETA = "Nadie podrá pagar con esta tarjeta hasta que la desbloquees."
DESBLOQUEAR_QR = "Se podrá volver a pagar con este código QR."


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
    # 1: tarjeta activa; 2: QR bloqueado; 3: tarjeta dada de baja (no sale en la ficha)
    h = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", disponible=200, credenciales=[
        {"id": 1, "tipo": "nfc", "bloqueada": False},
        {"id": 2, "tipo": "qr", "bloqueada": True},
        {"id": 3, "tipo": "nfc", "bloqueada": False, "activa": False}])
    assert entrar_tutor(c, "8341445807", "temporal123").status_code == 303
    return h


def _url(hijo, cred=1, accion="bloquear"):
    return f"/hijos/{hijo['id']}/credenciales/{cred}/{accion}"


def _estado(tpv, cred):
    return next(x["bloqueada"] for a in tpv.alumnos.values() for x in a["credenciales"]
                if x["id"] == cred)


def test_bloquear_y_desbloquear(db, tutor, hijo, c, tpv):
    g = c.get(_url(hijo))
    assert g.status_code == 200 and "no-store" in g.headers["cache-control"]
    assert BLOQUEAR_TARJETA in g.text
    o = _ocultos(g)
    assert len(o["llave"]) == 1 and "csrf" in o
    assert "credencial/bloquear" not in tpv.llamadas  # el GET no escribe
    r = c.post(_url(hijo), data=o)
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert BLOQUEAR_TARJETA in r.text and "listo" in r.text
    assert tpv.cuerpos_credencial == [("credencial/bloquear", {
        "credencial_id": 1, "llave": o["llave"][0], "tutor_ref": tutor.tutor_ref})]
    assert _estado(tpv, 1) is True
    fila = db.query(Bitacora).filter_by(operacion="credencial/bloquear").one()
    assert fila.actor == f"tutor:{tutor.id}" and fila.http_status == 200
    # la ficha lo muestra
    t = c.get(f"/hijos/{hijo['id']}").text
    assert "Bloqueada · nadie" in t and "Bloqueado · nadie" in t and "Activ" not in t
    # y se desbloquea el QR
    g2 = c.get(_url(hijo, 2, "desbloquear"))
    assert DESBLOQUEAR_QR in g2.text
    r2 = c.post(_url(hijo, 2, "desbloquear"), data=_ocultos(g2))
    assert DESBLOQUEAR_QR in r2.text
    assert tpv.cuerpos_credencial[-1][0] == "credencial/desbloquear"
    assert _estado(tpv, 2) is False and _estado(tpv, 1) is True  # una no toca la otra


def test_la_ficha_muestra_tipo_estado_y_boton(hijo, c):
    t = c.get(f"/hijos/{hijo['id']}").text
    assert "Tarjeta" in t and "Código QR" in t
    assert "Activa · puede pagar" in t and "Bloqueado · nadie" in t  # tarjeta activa, QR bloqueado
    assert _url(hijo, 1, "bloquear") in t and _url(hijo, 2, "desbloquear") in t
    assert _url(hijo, 1, "desbloquear") not in t and _url(hijo, 2, "bloquear") not in t
    assert "/credenciales/3/" not in t  # la dada de baja no sale


def test_credencial_de_otro_hijo_404_sin_llamar(db, op, tutor, hijo, c, tpv):
    hermano = tpv.agregar_hijo(tutor.tutor_ref, "Sofía Pérez", credenciales=[
        {"id": 10, "tipo": "nfc", "bloqueada": False}])
    ajeno = crear_tutor(db, op, telefono="8340000001")
    otro = tpv.agregar_hijo(ajeno.tutor_ref, "Ajeno", credenciales=[
        {"id": 20, "tipo": "nfc", "bloqueada": False}])
    o = _ocultos(c.get(_url(hijo)))
    for url in (_url(hijo, 10),            # credencial del hermano, por la URL de Luis
                _url(hermano, 1),          # credencial de Luis, por la URL de Sofía
                _url(hijo, 20), _url(otro, 20), _url(otro, 1),  # ajenos
                _url(hijo, 999),           # no existe
                _url(hijo, 3),             # dada de baja: no sale en la ficha
                "/hijos/999/credenciales/1/bloquear"):
        assert c.get(url).status_code == 404, url
        assert c.post(url, data=o).status_code == 404, url
    assert "credencial/bloquear" not in tpv.llamadas
    assert _estado(tpv, 10) is False and _estado(tpv, 20) is False and _estado(tpv, 3) is False


def test_accion_invalida_404(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo)))
    for accion in ("borrar", "BLOQUEAR", "x"):
        assert c.get(_url(hijo, 1, accion)).status_code == 404
        assert c.post(_url(hijo, 1, accion), data=o).status_code == 404
    assert tpv.cuerpos_credencial == []


def test_sin_csrf_403(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo)))
    o.pop("csrf")
    assert c.post(_url(hijo), data=o).status_code == 403
    o["csrf"] = ["malo"]
    assert c.post(_url(hijo), data=o).status_code == 403
    assert "credencial/bloquear" not in tpv.llamadas


def test_pide_sesion(op, c, tpv):
    assert c.get("/hijos/1/credenciales/1/bloquear").status_code == 303
    assert c.post("/hijos/1/credenciales/1/bloquear", data={}).status_code == 303


def test_bloquear_la_ya_bloqueada_dice_ya_estaba_asi_sin_escribir(hijo, c, tpv):
    g = c.get(_url(hijo, 2, "bloquear"))
    assert "Ya estaba así" in g.text and "llave" not in _ocultos(g)
    assert "Ya estaba así" in c.get(_url(hijo, 1, "desbloquear")).text
    o = _ocultos(c.get(_url(hijo)))
    tpv.alumnos[hijo["id"]]["credenciales"][0]["bloqueada"] = True  # la bloqueó el otro tutor
    r = c.post(_url(hijo), data=o)
    assert "Ya estaba así" in r.text and BLOQUEAR_TARJETA in r.text
    # se escribió (no-op en el TPV) para consumir la llave: repetir tras un desbloqueo posterior
    # no vuelve a bloquear ni desbloquear nada
    assert tpv.llamadas["credencial/bloquear"] == 1
    tpv.alumnos[hijo["id"]]["credenciales"][0]["bloqueada"] = False  # otro tutor la desbloqueó
    c.post(_url(hijo), data=o)
    assert tpv.alumnos[hijo["id"]]["credenciales"][0]["bloqueada"] is False


def test_doble_toque_ve_el_resultado_no_ya_estaba_asi(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo)))
    r1 = c.post(_url(hijo), data=o)
    r2 = c.post(_url(hijo), data=o)
    assert BLOQUEAR_TARJETA in r1.text and BLOQUEAR_TARJETA in r2.text
    assert "Ya estaba así" not in r1.text and "Ya estaba así" not in r2.text
    assert len(tpv.una_vez) == 1


def test_reintento_viejo_no_deshace_un_bloqueo_posterior(hijo, c, tpv):
    """El caso que motivó _sb_una_vez en el TPV: desbloquear (viejo) y luego bloquear; repetir el
    desbloqueo con su llave no debe volver a desbloquear ni mentirle al tutor."""
    o = _ocultos(c.get(_url(hijo, 2, "desbloquear")))
    assert DESBLOQUEAR_QR in c.post(_url(hijo, 2, "desbloquear"), data=o).text
    tpv.alumnos[hijo["id"]]["credenciales"][1]["bloqueada"] = True  # se bloqueó después
    t = c.post(_url(hijo, 2, "desbloquear"), data=o).text
    assert "el otro padre de familia lo haya cambiado" in t and DESBLOQUEAR_QR not in t
    assert _estado(tpv, 2) is True


def test_tpv_caido_reintenta_con_la_misma_llave(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo)))
    tpv.fallar_en = {"credencial/bloquear"}
    r = c.post(_url(hijo), data=o)
    assert "No se pudo contactar al TPV" in r.text and _ocultos(r)["llave"] == o["llave"]
    tpv.fallar_en = set()
    assert BLOQUEAR_TARJETA in c.post(_url(hijo), data=_ocultos(r)).text
    assert _estado(tpv, 1) is True


def test_respuesta_perdida_se_confirma_al_repetir(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo)))
    buena = tpv._credencial

    def se_pierde(op, d):
        buena(op, d)
        return httpx.Response(503, json={"ok": False, "error": {"codigo": "x", "mensaje": "x"}})

    tpv._credencial = se_pierde
    r = c.post(_url(hijo), data=o)
    assert "No se pudo contactar" in r.text and _estado(tpv, 1) is True
    tpv._credencial = buena
    t = c.post(_url(hijo), data=_ocultos(r)).text
    assert BLOQUEAR_TARJETA in t and "Ya estaba así" not in t


def test_llave_invalida_no_llama(hijo, c, tpv):
    for llave in ("", "x" * 121, "con espacio"):
        o = _ocultos(c.get(_url(hijo)))
        o["llave"] = [llave]
        assert "no es válida" in c.post(_url(hijo), data=o).text
    assert "credencial/bloquear" not in tpv.llamadas


def test_lecturas_caidas(hijo, c, tpv):
    tpv.fallar_en = {"tutor/alumnos"}
    g = c.get(_url(hijo))
    assert "No pudimos consultar" in g.text and "llave" not in _ocultos(g)


def test_llave_reutilizada_tiene_mensaje_propio(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo)))
    tpv._credencial = lambda op, d: httpx.Response(409, json={"ok": False, "error": {
        "codigo": "llave_reutilizada", "mensaje": "Esa llave ya se usó para otra operación."}})
    t = c.post(_url(hijo), data=o).text
    assert "vuelve a armarlo" not in t and "pedido" not in t.lower()
    assert "Vuelve a abrir" in t and o["llave"][0] not in t


def test_rechazo_final_se_muestra(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo)))
    tpv._credencial = lambda op, d: httpx.Response(404, json={"ok": False, "error": {
        "codigo": "credencial_desconocida", "mensaje": "Credencial desconocida."}})
    t = c.post(_url(hijo), data=o).text
    assert "Credencial desconocida." in t and BLOQUEAR_TARJETA not in t


def test_respuesta_con_otro_estado_no_es_exito(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo)))
    tpv._credencial = lambda op, d: httpx.Response(200, json={"ok": True, "alumno": {
        "credenciales": [{"id": 1, "tipo": "nfc", "bloqueada": False}]}})
    t = c.post(_url(hijo), data=o).text
    assert "respondió algo inesperado" in t and BLOQUEAR_TARJETA not in t
    tpv._credencial = lambda op, d: httpx.Response(200, json={"ok": True})
    assert "respondió algo inesperado" in c.post(_url(hijo), data=o).text


def test_rechazo_tras_intento_sin_respuesta_avisa(hijo, c, tpv):
    o = _ocultos(c.get(_url(hijo)))
    tpv.fallar_en = {"credencial/bloquear"}
    c.post(_url(hijo), data=o)
    tpv.fallar_en = set()
    tpv._credencial = lambda op, d: httpx.Response(404, json={"ok": False, "error": {
        "codigo": "credencial_desconocida", "mensaje": "Credencial desconocida."}})
    assert "puede haberse registrado" in c.post(_url(hijo), data=o).text


def test_vinculo_desactivado_corta_aun_repitiendo(db, op, tutor, hijo, c, tpv):
    """Sin vínculo con el hijo ya no se repite nada: 404."""
    o = _ocultos(c.get(_url(hijo)))
    assert BLOQUEAR_TARJETA in c.post(_url(hijo), data=o).text
    tpv.tutores[tutor.tutor_ref]["alumnos"] = []  # vínculo desactivado
    r = c.post(_url(hijo), data=o)
    assert r.status_code == 404
