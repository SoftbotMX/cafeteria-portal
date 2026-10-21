import hashlib
import hmac
import time

import httpx
import pytest

from portal.cifrado import cifrar
from portal.models import Bitacora, Operador
from portal.tpv import ClienteTPV, ErrorTPV, cliente_admin, cliente_tutor, firmar_tpv, registrar_escritura
from tests.conftest import FERNET_PRUEBAS
from tests.tpv_falso import TPVFalso

URL = "https://tpv.test"


@pytest.fixture(autouse=True)
def _limpiar_firmas_emitidas():
    import portal.tpv as m
    m._emitidas.clear()
    yield
    m._emitidas.clear()


def test_firma_tpv_coincide_con_el_contrato():
    esperado = hmac.new(b"s", b"1700000000./sb_cafe/api/v1/menu.{}", hashlib.sha256).hexdigest()
    assert firmar_tpv("s", "1700000000", "/sb_cafe/api/v1/menu", b"{}") == esperado


def test_llamar_ok_devuelve_cuerpo():
    f = TPVFalso()
    c = ClienteTPV(URL, "admin-s", transport=f.transport)
    r = c.llamar("admin/tutor/crear", {"nombre": "Ana ñ", "telefono": "8341445807", "llave": "k1"})
    assert r["ok"] is True and r["tutor"]["tutor_ref"] == "T1"
    assert f.llamadas["admin/tutor/crear"] == 1


def test_llamar_error_lanza_errortpv_con_codigo():
    f = TPVFalso()
    c = ClienteTPV(URL, "admin-s", transport=f.transport)
    c.llamar("admin/tutor/crear", {"nombre": "A", "llave": "k1"})
    with pytest.raises(ErrorTPV) as e:
        c.llamar("admin/tutor/crear", {"nombre": "B", "llave": "k1"})
    assert (e.value.status, e.value.codigo) == (409, "llave_reutilizada")


def test_misma_llave_mismo_contenido_devuelve_guardada_y_firma_cada_vez():
    f = TPVFalso()
    c = ClienteTPV(URL, "admin-s", transport=f.transport)
    a = c.llamar("admin/tutor/crear", {"nombre": "A", "llave": "k1"})
    b = c.llamar("admin/tutor/crear", {"nombre": "A", "llave": "k1"})
    assert a == b and len(f.tutores) == 1 and len(f.firmas_vistas) == 2


def test_firma_reutilizada_a_mano_da_409():
    f = TPVFalso()
    ts = str(int(time.time()))
    ruta = "/sb_cafe/api/v1/menu"
    cab = {"X-SB-Cafe-Timestamp": ts, "X-SB-Cafe-Signature": "hmac-sha256=" + firmar_tpv("tutor-s", ts, ruta, b"{}")}
    with httpx.Client(transport=f.transport) as h:
        assert h.post(URL + ruta, content=b"{}", headers=cab).status_code == 200
        assert h.post(URL + ruta, content=b"{}", headers=cab).status_code == 409


def test_secreto_equivocado_401():
    f = TPVFalso()
    c = ClienteTPV(URL, "otro", transport=f.transport)
    with pytest.raises(ErrorTPV) as e:
        c.llamar("menu", {})
    assert e.value.status == 401


def test_sin_conexion():
    def boom(req):
        raise httpx.ConnectError("no", request=req)
    c = ClienteTPV(URL, "s", transport=httpx.MockTransport(boom))
    with pytest.raises(ErrorTPV) as e:
        c.llamar("menu", {})
    assert (e.value.status, e.value.codigo) == (0, "sin_conexion")


def test_respuesta_no_json_es_respuesta_invalida():
    c = ClienteTPV(URL, "s", transport=httpx.MockTransport(lambda r: httpx.Response(502, text="<html>bad gateway</html>")))
    with pytest.raises(ErrorTPV) as e:
        c.llamar("menu", {})
    assert (e.value.status, e.value.codigo) == (502, "respuesta_invalida")


def test_200_no_json_tambien_es_invalida():
    c = ClienteTPV(URL, "s", transport=httpx.MockTransport(lambda r: httpx.Response(200, text="hola")))
    with pytest.raises(ErrorTPV) as e:
        c.llamar("menu", {})
    assert e.value.codigo == "respuesta_invalida"


def test_503_del_falso():
    f = TPVFalso()
    f.fallar_en.add("admin/tutor/crear")
    c = ClienteTPV(URL, "admin-s", transport=f.transport)
    with pytest.raises(ErrorTPV) as e:
        c.llamar("admin/tutor/crear", {"nombre": "A", "llave": "k"})
    assert e.value.status == 503


def test_error_no_filtra_secreto():
    f = TPVFalso()
    c = ClienteTPV(URL, "sec-ultra", transport=f.transport)
    with pytest.raises(ErrorTPV) as e:
        c.llamar("menu", {})
    assert "sec-ultra" not in str(e.value) and "sec-ultra" not in repr(e.value)
    assert "sec-ultra" not in repr(c) and "sec-ultra" not in str(c)


def _operador(db):
    op = Operador(nombre="X", host="h", tpv_url=URL,
                  tpv_secreto_cif=cifrar("tutor-s", FERNET_PRUEBAS),
                  tpv_secreto_admin_cif=cifrar("admin-s", FERNET_PRUEBAS))
    db.add(op)
    db.commit()
    return op


def test_cliente_admin_y_tutor_usan_su_secreto(db, app):
    f = TPVFalso()
    app.state.tpv_transport = f.transport
    op = _operador(db)
    r = cliente_admin(app, op, FERNET_PRUEBAS).llamar("admin/tutor/crear", {"nombre": "A", "llave": "k"})
    assert r["ok"]
    assert cliente_tutor(app, op, FERNET_PRUEBAS).llamar("menu", {})["productos"] == []


def test_cliente_admin_no_sirve_para_tutor(db, app):
    f = TPVFalso()
    app.state.tpv_transport = f.transport
    op = _operador(db)
    with pytest.raises(ErrorTPV) as e:
        cliente_admin(app, op, FERNET_PRUEBAS).llamar("menu", {})
    assert e.value.status == 401


def test_registrar_escritura(db):
    op = _operador(db)
    registrar_escritura(db, op.id, "admin:1", "admin/tutor/crear", "k1", None)
    registrar_escritura(db, op.id, "admin:1", "admin/tutor/crear", "k2", ErrorTPV(409, "llave_reutilizada", "x"))
    db.rollback()  # flush, no commit: la transacción es de quien llama
    assert db.query(Bitacora).count() == 0
    registrar_escritura(db, op.id, "admin:1", "admin/tutor/crear", "k1", None)
    registrar_escritura(db, op.id, "admin:1", "admin/tutor/crear", "k2", ErrorTPV(409, "llave_reutilizada", "x"))
    filas = db.query(Bitacora).order_by(Bitacora.id).all()
    assert [(b.http_status, b.codigo_error) for b in filas] == [(200, None), (409, "llave_reutilizada")]


def test_llamadas_identicas_en_el_mismo_segundo_firman_distinto(monkeypatch):
    monkeypatch.setattr("portal.tpv.time.time", lambda: 1_700_000_000.0)
    vistas = []

    def h(req):
        vistas.append((req.headers["X-SB-Cafe-Timestamp"], req.headers["X-SB-Cafe-Signature"]))
        return httpx.Response(200, json={"ok": True})
    c = ClienteTPV(URL, "s", transport=httpx.MockTransport(h))
    c.llamar("menu", {}); c.llamar("menu", {})
    assert vistas[0][1] != vistas[1][1] and int(vistas[1][0]) > int(vistas[0][0])


def test_trafico_sostenido_no_se_sale_de_la_ventana(monkeypatch):
    monkeypatch.setattr("portal.tpv.time.time", lambda: 1_800_000_000.0)
    stamps = []
    c = ClienteTPV(URL, "s-ventana", transport=httpx.MockTransport(
        lambda r: stamps.append(int(r.headers["X-SB-Cafe-Timestamp"])) or httpx.Response(200, json={"ok": True})))
    c.llamar("menu", {}); c.llamar("menu", {})
    c.llamar("menu", {"a": 1})  # otro cuerpo: no necesita adelantarse
    assert stamps == [1_800_000_000, 1_800_000_001, 1_800_000_000]


def test_repetida_por_otro_worker_se_reintenta_firmando_de_nuevo(monkeypatch):
    import portal.tpv as m
    monkeypatch.setattr("portal.tpv.time.time", lambda: 1_900_000_000.0)
    f = TPVFalso(secreto_tutor="s-multi")
    ts = "1900000000"
    # otro worker ya gastó la firma de este segundo
    f.firmas_vistas.add("hmac-sha256=" + firmar_tpv("s-multi", ts, "/sb_cafe/api/v1/menu", b"{}"))
    m._emitidas.clear()
    r = ClienteTPV(URL, "s-multi", transport=f.transport).llamar("menu", {})
    assert r["ok"] and f.llamadas["menu"] == 1


def _alta_tutor(c, llave="kt"):
    return c.llamar("admin/tutor/crear", {"nombre": "A", "llave": llave})


def test_reintento_de_tutor_crear_devuelve_la_respuesta_original():
    f = TPVFalso()
    c = ClienteTPV(URL, "admin-s", transport=f.transport)
    ref = _alta_tutor(c)["tutor"]["tutor_ref"]
    c.llamar("admin/alumno/crear", {"nombre": "H", "tutores": [ref], "llave": "ka"})
    assert f.tutores[ref]["alumnos"]
    assert _alta_tutor(c)["tutor"]["alumnos"] == []


def test_reintento_de_alumno_crear_devuelve_la_respuesta_original():
    f = TPVFalso()
    c = ClienteTPV(URL, "admin-s", transport=f.transport)
    ref = _alta_tutor(c)["tutor"]["tutor_ref"]
    cuerpo = {"nombre": "H", "tutores": [ref], "llave": "ka"}
    al = c.llamar("admin/alumno/crear", cuerpo)["alumno"]
    f.alumnos[al["id"]]["disponible"] = 500  # estado vivo cambia después
    assert c.llamar("admin/alumno/crear", cuerpo)["alumno"]["disponible"] == 0
