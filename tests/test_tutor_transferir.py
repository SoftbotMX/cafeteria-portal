from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from portal.formato import parse_importe
from portal.models import Bitacora
from portal.reglas import ahora_utc
from tests.fabricas import crear_operador, crear_tutor, csrf_de, entrar_tutor
from tests.test_tutor_pedir import _ocultos
from tests.tpv_falso import TPVFalso

AHORA = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def op(db):
    return crear_operador(db)


@pytest.fixture
def tpv(app):
    f = TPVFalso(secreto_tutor="secreto-tutor")
    f.hoy = date(2026, 10, 21)
    app.state.tpv_transport = f.transport
    return f


@pytest.fixture
def c(app):
    app.dependency_overrides[ahora_utc] = lambda: AHORA
    yield TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)
    app.dependency_overrides.pop(ahora_utc, None)


@pytest.fixture
def tutor(db, op):
    return crear_tutor(db, op, debe_cambiar=False)


@pytest.fixture
def dos(tutor, tpv, c):
    luis = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", disponible=200)
    ana = tpv.agregar_hijo(tutor.tutor_ref, "Ana Pérez", disponible=50)
    assert entrar_tutor(c, "8341445807", "temporal123").status_code == 303
    return luis, ana


def _revisar(c, origen, destino, importe, csrf=None):
    if csrf is None:
        csrf = csrf_de(c.get("/transferir"))
    return c.post("/transferir/revisar", data={
        "csrf": csrf, "origen_id": str(origen), "destino_id": str(destino), "importe": importe})


def _confirmacion(c, origen, destino, importe):
    r = _revisar(c, origen["id"], destino["id"], importe)
    assert r.status_code == 200 and "/transferir/confirmar" in r.text, r.text
    return _ocultos(r)


def test_parse_importe():
    for texto, esperado in [("150", "150.00"), ("150.5", "150.50"), ("150.50", "150.50"),
                            ("$1,500.00", "1500.00"), (" 20 ", "20.00"), ("0.01", "0.01"),
                            ("$ 7", "7.00")]:
        v = parse_importe(texto)
        assert v == Decimal(esperado) and v.as_tuple().exponent == -2, texto
    for malo in ["", "  ", None, "abc", "0", "0.00", "-5", "1.234", "1.", ".5", "1,50", "1e3",
                 "NaN", "Infinity", "12,34,567", "١٢٣", "1 000", "9999999999", "15$", "999,999,999,999,999,999,999,999,999,999", "1,000,000,000"]:
        assert parse_importe(malo) is None, malo


def test_transferir_mueve_saldo(db, tpv, c, dos):
    luis, ana = dos
    r = c.get("/transferir")
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert "Luis Pérez" in r.text and "Ana Pérez" in r.text
    r = _revisar(c, luis["id"], ana["id"], "$75.50")
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert "$75.50" in r.text and "Luis Pérez" in r.text and "Ana Pérez" in r.text
    assert "transferencia" not in tpv.llamadas  # revisar no escribe
    o = _ocultos(r)
    assert o["origen_id"] == [str(luis["id"])] and o["destino_id"] == [str(ana["id"])]
    assert o["importe"] == ["75.50"] and 8 <= len(o["llave"][0]) <= 120
    r = c.post("/transferir/confirmar", data=o)
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert tpv.alumnos[luis["id"]]["disponible"] == 124.5
    assert tpv.alumnos[ana["id"]]["disponible"] == 125.5
    # los saldos nuevos salen de la respuesta del TPV
    assert "$124.50" in r.text and "$125.50" in r.text
    assert tpv.cuerpos_transferencia[0]["importe"] == 75.5
    assert tpv.cuerpos_transferencia[0]["origen_id"] == luis["id"]
    assert tpv.cuerpos_transferencia[0]["tutor_ref"]
    fila = db.query(Bitacora).filter_by(operacion="transferencia").one()
    assert fila.http_status == 200


def test_importe_se_manda_como_numero_sin_ruido(tpv, c, dos):
    luis, ana = dos
    c.post("/transferir/confirmar", data=_confirmacion(c, luis, ana, "0.10"))
    c.post("/transferir/confirmar", data=_confirmacion(c, luis, ana, "$19.99"))
    assert [b["importe"] for b in tpv.cuerpos_transferencia] == [0.1, 19.99]
    assert all(isinstance(b["importe"], float) for b in tpv.cuerpos_transferencia)


def test_doble_confirmar_una_transferencia(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "100")
    r1 = c.post("/transferir/confirmar", data=o)
    # el segundo toque llega con el origen ya sin saldo suficiente: ve el resultado, no un error
    r2 = c.post("/transferir/confirmar", data=o)
    assert r1.status_code == r2.status_code == 200
    assert "Listo" in r1.text and "Listo" in r2.text
    assert tpv.alumnos[luis["id"]]["disponible"] == 100 and tpv.alumnos[ana["id"]]["disponible"] == 150
    assert len([m for m in tpv.movimientos[luis["id"]] if m["tipo"] == "transferencia"]) == 1
    assert "$100.00" in r2.text and "$150.00" in r2.text  # saldos actuales


def test_doble_confirmar_con_saldo_gastado_despues_no_da_error(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "150")
    c.post("/transferir/confirmar", data=o)
    tpv.alumnos[luis["id"]]["disponible"] = 10  # gastó el resto
    r = c.post("/transferir/confirmar", data=o)
    assert "Listo" in r.text and "$10.00" in r.text
    assert len(tpv.transferencias) == 1 and tpv.alumnos[ana["id"]]["disponible"] == 200


@pytest.mark.parametrize("malo", ["abc", "0", "-5", "1.234", "", "0.00", "1e2"])
def test_importes_invalidos(tpv, c, dos, malo):
    luis, ana = dos
    r = _revisar(c, luis["id"], ana["id"], malo)
    assert r.status_code == 200 and "/transferir/confirmar" not in r.text
    assert "importe" in r.text.lower() and "Luis Pérez" in r.text  # vuelve al formulario con error
    assert "transferencia" not in tpv.llamadas


def test_confirmar_con_importe_invalido_no_llama(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "10")
    for malo in ["1.234", "abc", "-1", ""]:
        r = c.post("/transferir/confirmar", data=o | {"importe": [malo]})
        assert "/transferir" in r.text and r.status_code == 200
    assert "transferencia" not in tpv.llamadas


def test_origen_igual_destino(tpv, c, dos):
    luis, _ = dos
    r = _revisar(c, luis["id"], luis["id"], "10")
    assert r.status_code == 200 and "distintos" in r.text and "/transferir/confirmar" not in r.text
    o = _confirmacion(c, *dos, "10")
    o["destino_id"] = o["origen_id"]
    r = c.post("/transferir/confirmar", data=o)
    assert "distintos" in r.text
    assert "transferencia" not in tpv.llamadas


def test_mas_que_el_disponible_rechazado_en_revisar(tpv, c, dos):
    luis, ana = dos
    r = _revisar(c, ana["id"], luis["id"], "50.01")  # Ana tiene $50.00
    assert r.status_code == 200 and "/transferir/confirmar" not in r.text
    assert "no tiene" in r.text and "$50.00" in r.text
    assert "transferencia" not in tpv.llamadas
    assert "/transferir/confirmar" in _revisar(c, ana["id"], luis["id"], "50").text  # justo el disponible


def test_rechazo_de_saldo_en_confirmar_usa_el_mensaje_del_tpv(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "150")
    buena = tpv._transferencia

    def se_gasta_antes(d):  # se gastó entre la lectura del portal y la escritura
        tpv.alumnos[luis["id"]]["disponible"] = 20
        return buena(d)

    tpv._transferencia = se_gasta_antes
    r = c.post("/transferir/confirmar", data=o)
    assert "Saldo insuficiente de Luis Pérez" in r.text
    assert tpv.alumnos[ana["id"]]["disponible"] == 50


def test_hijo_ajeno_404_sin_llamar(db, op, tpv, c, dos):
    luis, ana = dos
    ajeno = crear_tutor(db, op, telefono="8340000001")
    otro = tpv.agregar_hijo(ajeno.tutor_ref, "Ajeno", disponible=500)
    csrf = csrf_de(c.get("/transferir"))
    for o, d in [(otro["id"], ana["id"]), (luis["id"], otro["id"]), (9999, ana["id"])]:
        assert _revisar(c, o, d, "10", csrf).status_code == 404
    assert _revisar(c, "abc", ana["id"], "10", csrf).status_code == 404
    ocultos = _confirmacion(c, luis, ana, "10")
    for campo in ("origen_id", "destino_id"):
        malo = ocultos | {campo: [str(otro["id"])]}
        assert c.post("/transferir/confirmar", data=malo).status_code == 404
    assert "transferencia" not in tpv.llamadas
    assert tpv.alumnos[otro["id"]]["disponible"] == 500


def test_un_solo_hijo_sin_boton_y_redirige(tutor, tpv, c):
    tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", disponible=200)
    assert entrar_tutor(c, "8341445807", "temporal123").status_code == 303
    r = c.get("/transferir")
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_boton_solo_con_dos_o_mas_hijos(tpv, c, dos):
    assert "/transferir" in c.get("/").text and "Transferir saldo" in c.get("/").text


def test_sin_hijos_redirige(tutor, tpv, c):
    tpv.agregar_tutor(tutor.tutor_ref)
    assert entrar_tutor(c, "8341445807", "temporal123").status_code == 303
    assert c.get("/transferir").status_code == 303


def test_sin_csrf_403(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "10")
    for csrf in (None, "malo"):
        d = {k: v for k, v in o.items() if k != "csrf"}
        if csrf:
            d["csrf"] = csrf
        assert c.post("/transferir/confirmar", data=d).status_code == 403
        assert c.post("/transferir/revisar", data={k: v[0] for k, v in d.items()}
                      | {"importe": "10"}).status_code == 403
    assert "transferencia" not in tpv.llamadas


def test_llave_invalida_no_llama(tpv, c, dos):
    luis, ana = dos
    for llave in ("", "x" * 121, "con espacio"):
        o = _confirmacion(c, luis, ana, "10") | {"llave": [llave]}
        assert "no es válida" in c.post("/transferir/confirmar", data=o).text
    assert "transferencia" not in tpv.llamadas


def test_tpv_caido_reintenta_con_la_misma_llave(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "30")
    tpv.fallar_en = {"transferencia"}
    r = c.post("/transferir/confirmar", data=o)
    assert "No se pudo contactar" in r.text and _ocultos(r)["llave"] == o["llave"]
    assert _ocultos(r)["importe"] == ["30.00"] or _ocultos(r)["importe"] == ["30"]
    tpv.fallar_en = set()
    r2 = c.post("/transferir/confirmar", data=_ocultos(r))
    assert "Listo" in r2.text and tpv.alumnos[luis["id"]]["disponible"] == 170


def test_respuesta_perdida_se_confirma_al_repetir_sin_duplicar(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "40")
    buena = tpv._transferencia

    def se_pierde(d):
        buena(d)  # el TPV sí transfiere...
        return httpx.Response(503, json={"ok": False, "error": {"codigo": "x", "mensaje": "x"}})

    tpv._transferencia = se_pierde
    r = c.post("/transferir/confirmar", data=o)
    assert "No se pudo contactar" in r.text
    tpv._transferencia = buena
    r2 = c.post("/transferir/confirmar", data=_ocultos(r))
    assert "Listo" in r2.text and tpv.alumnos[luis["id"]]["disponible"] == 160
    assert len(tpv.transferencias) == 1


def test_rechazo_tras_intento_incierto_avisa(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "40")
    tpv.fallar_en = {"transferencia"}
    c.post("/transferir/confirmar", data=o)
    tpv.fallar_en = set()
    buena = tpv._transferencia
    tpv._transferencia = lambda d: (tpv.alumnos[luis["id"]].update(disponible=5), buena(d))[1]
    r = c.post("/transferir/confirmar", data=o)
    assert "Saldo insuficiente" in r.text and "puede haberse registrado" in r.text


def test_misma_llave_con_otro_contenido_no_se_aplica(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "40")
    c.post("/transferir/confirmar", data=o)
    r = c.post("/transferir/confirmar", data=o | {"importe": ["41"]})
    assert "cambió desde que la revisaste" in r.text and "portal:" not in r.text
    assert tpv.alumnos[luis["id"]]["disponible"] == 160 and len(tpv.transferencias) == 1


def test_respuesta_inesperada_no_es_exito(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "40")
    tpv._transferencia = lambda d: httpx.Response(200, json={"ok": True, "alumnos": []})
    r = c.post("/transferir/confirmar", data=o)
    assert "Listo" not in r.text and "inesperado" in r.text


def test_lecturas_caidas_al_confirmar_conservan_la_llave(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "40")
    tpv.fallar_en = {"tutor/alumnos"}
    r = c.post("/transferir/confirmar", data=o)
    assert _ocultos(r)["llave"] == o["llave"] and "transferencia" not in tpv.llamadas
    tpv.fallar_en = set()
    assert "Listo" in c.post("/transferir/confirmar", data=_ocultos(r)).text


def test_transferir_pide_sesion(op, c, tpv):
    assert c.get("/transferir").status_code == 303
    assert c.post("/transferir/revisar", data={}).status_code == 303
    assert c.post("/transferir/confirmar", data={}).status_code == 303


def test_confirmar_revisa_el_disponible_fresco_sin_llamar(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "150")
    tpv.alumnos[luis["id"]]["disponible"] = 20
    r = c.post("/transferir/confirmar", data=o)
    assert "no tiene tanto" in r.text and "$20.00" in r.text
    assert "transferencia" not in tpv.llamadas


def test_respuesta_perdida_con_saldo_ya_descontado_se_reenvia_con_la_misma_llave(tpv, c, dos):
    luis, ana = dos
    o = _confirmacion(c, luis, ana, "150")  # de $200: tras aplicarse quedan $50 < $150
    buena = tpv._transferencia

    def se_pierde(d):
        buena(d)
        return httpx.Response(503, json={"ok": False, "error": {"codigo": "x", "mensaje": "x"}})

    tpv._transferencia = se_pierde
    r = c.post("/transferir/confirmar", data=o)
    tpv._transferencia = buena
    r2 = c.post("/transferir/confirmar", data=_ocultos(r))
    assert "Listo" in r2.text and "$50.00" in r2.text and "$200.00" in r2.text
    assert len(tpv.transferencias) == 1 and tpv.llamadas["transferencia"] == 2


def test_importe_gigante_con_comas_no_da_500(tpv, c, dos):
    luis, ana = dos
    r = _revisar(c, luis["id"], ana["id"], "999," * 12 + "999")
    assert r.status_code == 200 and "importe" in r.text.lower()
