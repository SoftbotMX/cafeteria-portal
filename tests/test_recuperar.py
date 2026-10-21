import hashlib
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from portal.models import CodigoRecuperacion, IntentoLogin, Sesion
from portal.seguridad import verificar_contrasena
from tests.conduit_falso import ConduitFalso
from tests.fabricas import crear_operador, crear_tutor, csrf_de, entrar_tutor

MISMA = "Si el usuario existe, te mandamos un código"


@pytest.fixture
def op(db):
    return crear_operador(db)


@pytest.fixture
def conduit(app):
    f = ConduitFalso(secreto="secreto-conduit")
    app.state.conduit_transport = f.transport
    return f


@pytest.fixture
def c(app, conduit):
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


def _pedir(c, usuario):
    pagina = c.get("/recuperar")
    return c.post("/recuperar", data={"usuario": usuario, "csrf": csrf_de(pagina)})


def _codigo_enviado(conduit) -> str:
    return re.search(r"es (\d{6})\.", conduit.enviados[-1]["text"]).group(1)


def _cambiar(c, usuario, codigo, nueva="nueva-clave-1", confirmar=None):
    pagina = c.get("/recuperar/codigo", params={"u": usuario})
    return c.post("/recuperar/codigo", data={
        "usuario": usuario, "codigo": codigo, "nueva": nueva,
        "confirmar": confirmar or nueva, "csrf": csrf_de(pagina)})


def test_recuperar_usuario_inexistente_misma_respuesta(db, op, c, app, conduit):
    crear_tutor(db, op)
    a = _pedir(c, "8341445807")
    b = _pedir(TestClient(app, base_url="https://cafeteria.test", follow_redirects=False),
               "8340000000")
    assert a.status_code == b.status_code == 303
    assert a.headers["location"].split("?")[0] == b.headers["location"].split("?")[0] == "/recuperar/codigo"
    pa = c.get(a.headers["location"])
    assert MISMA in pa.text
    assert len(conduit.enviados) == 1
    assert db.query(CodigoRecuperacion).count() == 1


def test_recuperar_tutor_inactivo_no_recibe_codigo(db, op, c, conduit):
    crear_tutor(db, op, activo=False)
    r = _pedir(c, "8341445807")
    assert r.status_code == 303 and conduit.enviados == []
    assert db.query(CodigoRecuperacion).count() == 0


def test_recuperar_envia_codigo_por_whatsapp(db, op, c, conduit):
    t = crear_tutor(db, op)
    r = _pedir(c, "(834) 144-5807")
    assert r.status_code == 303
    assert "u=8341445807" in r.headers["location"]
    assert conduit.enviados[0]["recipient"] == "528341445807"
    texto = conduit.enviados[0]["text"]
    cod = _codigo_enviado(conduit)
    assert texto == (f"Tu código para cambiar tu contraseña del portal de la cafetería es {cod}. "
                     "Vence en 15 minutos.")
    fila = db.query(CodigoRecuperacion).one()
    assert fila.tutor_id == t.id and fila.codigo_hash == hashlib.sha256(cod.encode()).hexdigest()
    assert cod not in c.get(r.headers["location"]).text
    assert conduit.enviados[0]["idempotency_key"] == f"recuperar:{fila.id}"


def test_codigo_nuevo_invalida_el_anterior(db, op, c, conduit):
    crear_tutor(db, op)
    _pedir(c, "8341445807")
    viejo = _codigo_enviado(conduit)
    _pedir(c, "8341445807")
    nuevo = _codigo_enviado(conduit)
    assert db.query(CodigoRecuperacion).filter_by(usado=False).count() == 1
    if viejo != nuevo:
        assert _cambiar(c, "8341445807", viejo).status_code == 400
    assert _cambiar(c, "8341445807", nuevo).status_code == 303


def test_codigo_correcto_cambia_contrasena(db, op, c, app, conduit):
    t = crear_tutor(db, op, debe_cambiar=True)
    otra = TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)
    entrar_tutor(otra, "8341445807", "temporal123")
    assert db.query(Sesion).count() == 1
    _pedir(c, "8341445807")
    r = _cambiar(c, "8341445807", _codigo_enviado(conduit))
    assert r.status_code == 303 and r.headers["location"] == "/entrar?cambiada=1"
    db.expire_all()
    assert verificar_contrasena(t.contrasena_hash, "nueva-clave-1")
    assert t.debe_cambiar is False
    assert db.query(Sesion).count() == 0
    assert db.query(CodigoRecuperacion).one().usado is True
    assert "Tu contraseña cambió" in c.get("/entrar", params={"cambiada": "1"}).text
    assert entrar_tutor(c, "8341445807", "nueva-clave-1").headers["location"] == "/"


def test_codigo_expirado_no_vale(db, op, c, conduit):
    t = crear_tutor(db, op)
    _pedir(c, "8341445807")
    fila = db.query(CodigoRecuperacion).one()
    fila.expira = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    r = _cambiar(c, "8341445807", _codigo_enviado(conduit))
    assert r.status_code == 400
    db.expire_all()
    assert not verificar_contrasena(t.contrasena_hash, "nueva-clave-1")


def test_quinto_fallo_quema_el_codigo(db, op, c, conduit):
    t = crear_tutor(db, op)
    _pedir(c, "8341445807")
    bueno = _codigo_enviado(conduit)
    malo = "000000" if bueno != "000000" else "111111"
    for _ in range(5):
        assert _cambiar(c, "8341445807", malo).status_code == 400
    fila = db.query(CodigoRecuperacion).one()
    db.refresh(fila)
    assert fila.intentos == 5 and fila.usado is True
    assert _cambiar(c, "8341445807", bueno).status_code in (400, 429)
    db.expire_all()
    assert not verificar_contrasena(t.contrasena_hash, "nueva-clave-1")
    assert db.query(IntentoLogin).filter_by(exito=False).count() >= 5


def test_codigo_no_se_reusa(db, op, c, conduit):
    t = crear_tutor(db, op)
    _pedir(c, "8341445807")
    cod = _codigo_enviado(conduit)
    assert _cambiar(c, "8341445807", cod).status_code == 303
    r = _cambiar(c, "8341445807", cod, nueva="otra-clave-99")
    assert r.status_code == 400
    db.expire_all()
    assert verificar_contrasena(t.contrasena_hash, "nueva-clave-1")


def test_contrasena_nueva_invalida_no_gasta_el_codigo(db, op, c, conduit):
    crear_tutor(db, op)
    _pedir(c, "8341445807")
    cod = _codigo_enviado(conduit)
    assert _cambiar(c, "8341445807", cod, nueva="corta").status_code == 400
    assert _cambiar(c, "8341445807", cod, confirmar="distinta-123").status_code == 400
    assert db.query(CodigoRecuperacion).one().intentos == 0
    assert _cambiar(c, "8341445807", cod).status_code == 303


def test_maximo_tres_solicitudes_por_usuario(db, op, c, conduit):
    crear_tutor(db, op)
    for _ in range(3):
        assert _pedir(c, "8341445807").status_code == 303
    r = _pedir(c, "8341445807")
    assert r.status_code == 429 and len(conduit.enviados) == 3
    # el límite del login (5) no se ve afectado por las solicitudes de otro usuario
    assert _pedir(c, "8340000000").status_code == 303


def test_solicitudes_no_bloquean_el_login(db, op, c, conduit):
    crear_tutor(db, op, debe_cambiar=False)
    for _ in range(3):
        _pedir(c, "8341445807")
    assert entrar_tutor(c, "8341445807", "temporal123").status_code == 303


def test_csrf_invalido_en_ambos_formularios(db, op, c):
    crear_tutor(db, op)
    c.get("/recuperar")
    r = c.post("/recuperar", data={"usuario": "8341445807", "csrf": "viejo"})
    assert r.status_code == 403 and "Tu sesión expiró, vuelve a intentarlo." in r.text
    r = c.post("/recuperar/codigo", data={"usuario": "8341445807", "codigo": "123456",
                                          "nueva": "nueva-clave-1", "confirmar": "nueva-clave-1",
                                          "csrf": "viejo"})
    assert r.status_code == 403 and "Tu sesión expiró, vuelve a intentarlo." in r.text


def test_usuario_en_url_se_escapa(op, c):
    r = c.get("/recuperar/codigo", params={"u": '"><script>x</script>'})
    assert "<script>x</script>" not in r.text


def test_falla_el_envio_misma_respuesta(db, op, c, conduit):
    crear_tutor(db, op)
    conduit.responder = 500
    r = _pedir(c, "8341445807")
    assert r.status_code == 303


def test_enlace_en_entrar(op, c):
    assert "/recuperar" in c.get("/entrar").text
