import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from portal.models import IntentoLogin, Sesion, Tutor
from portal.seguridad import verificar_contrasena
from tests.fabricas import crear_operador, crear_tutor, csrf_de, entrar_tutor

MALO = "Usuario o contraseña incorrectos"


@pytest.fixture
def op(db):
    return crear_operador(db)


@pytest.fixture
def c(app):
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


def _c2(app):
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


def test_entrar_con_telefono_formateado(db, op, c):
    crear_tutor(db, op, debe_cambiar=False)
    r = entrar_tutor(c, "(834) 144-5807", "temporal123")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "Ana López" in c.get("/").text


def test_entrar_con_correo_en_mayusculas(db, op, c):
    crear_tutor(db, op, telefono=None, correo="ana@mail.com", debe_cambiar=False)
    r = entrar_tutor(c, " ANA@Mail.com ", "temporal123")
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_contrasena_mala_mismo_mensaje_que_usuario_inexistente(db, op, c, app):
    crear_tutor(db, op)
    a = entrar_tutor(c, "8341445807", "mala-mala")
    b = entrar_tutor(_c2(app), "8340000000", "mala-mala")
    assert a.status_code == b.status_code == 200
    assert MALO in a.text and MALO in b.text
    assert db.query(IntentoLogin).filter_by(exito=False).count() == 2


def test_tutor_inactivo_mismo_mensaje_y_cuenta_fallo(db, op, c):
    crear_tutor(db, op, activo=False)
    r = entrar_tutor(c, "8341445807", "temporal123")
    assert r.status_code == 200 and MALO in r.text
    assert db.query(IntentoLogin).filter_by(exito=False).count() == 1


def test_exito_registra_intento_exitoso(db, op, c):
    crear_tutor(db, op)
    entrar_tutor(c, "8341445807", "temporal123")
    assert db.query(IntentoLogin).filter_by(exito=True).count() == 1


def test_sexto_intento_429(db, op, c):
    crear_tutor(db, op)
    for _ in range(5):
        assert entrar_tutor(c, "8341445807", "mala-mala").status_code == 200
    r = entrar_tutor(c, "8341445807", "temporal123")  # aun con la buena
    assert r.status_code == 429
    assert "Demasiados intentos. Espera 15 minutos." in r.text


def test_temporal_obliga_a_cambiar(db, op, c):
    crear_tutor(db, op)
    r = entrar_tutor(c, "8341445807", "temporal123")
    assert r.headers["location"] == "/cambiar-contrasena"
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/cambiar-contrasena"
    assert c.get("/cambiar-contrasena").status_code == 200


def test_sin_sesion_va_a_entrar(op, c):
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/entrar"


def _cambiar(c, actual, nueva, confirmar, csrf=None):
    pagina = c.get("/cambiar-contrasena")
    return c.post("/cambiar-contrasena", data={
        "actual": actual, "nueva": nueva, "confirmar": confirmar,
        "csrf": csrf or csrf_de(pagina),
    })


def test_cambio_requiere_actual_correcta_y_confirmacion(db, op, c):
    t = crear_tutor(db, op)
    entrar_tutor(c, "8341445807", "temporal123")
    r = _cambiar(c, "otra-cosa", "nueva-clave-1", "nueva-clave-1")
    assert r.status_code == 400 and "La contraseña actual no es correcta." in r.text
    r = _cambiar(c, "temporal123", "nueva-clave-1", "distinta-123")
    assert r.status_code == 400 and "no coincide" in r.text
    r = _cambiar(c, "temporal123", "corta", "corta")
    assert r.status_code == 400 and "al menos 8 caracteres" in r.text
    r = _cambiar(c, "temporal123", "temporal123", "temporal123")
    assert r.status_code == 400 and "distinta" in r.text
    db.refresh(t)
    assert t.debe_cambiar is True
    r = _cambiar(c, "temporal123", "nueva-clave-1", "nueva-clave-1")
    assert r.status_code == 303 and r.headers["location"] == "/"
    db.refresh(t)
    assert t.debe_cambiar is False
    assert verificar_contrasena(t.contrasena_hash, "nueva-clave-1")
    assert c.get("/").status_code == 200


def test_cambio_actual_incorrecta_tiene_limite(db, op, c):
    crear_tutor(db, op)
    entrar_tutor(c, "8341445807", "temporal123")
    for _ in range(5):
        assert _cambiar(c, "mala", "nueva-clave-1", "nueva-clave-1").status_code == 400
    assert _cambiar(c, "temporal123", "nueva-clave-1", "nueva-clave-1").status_code == 429


def test_cambio_cierra_otras_sesiones(db, op, c, app):
    crear_tutor(db, op)
    otro = _c2(app)
    entrar_tutor(otro, "8341445807", "temporal123")
    entrar_tutor(c, "8341445807", "temporal123")
    assert _cambiar(c, "temporal123", "nueva-clave-1", "nueva-clave-1").status_code == 303
    r = otro.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/entrar"
    assert c.get("/").status_code == 200


def test_post_sin_csrf_403(db, op, c):
    crear_tutor(db, op, debe_cambiar=False)
    # login: sin campo, con campo que no coincide con la cookie, y sin cookie
    c.get("/entrar")
    d = {"usuario": "8341445807", "contrasena": "temporal123"}
    assert c.post("/entrar", data=d).status_code == 403
    assert c.post("/entrar", data={**d, "csrf": "falso"}).status_code == 403
    sin_cookie = TestClient(c.app, base_url="https://cafeteria.test", follow_redirects=False)
    assert sin_cookie.post("/entrar", data={**d, "csrf": ""}).status_code == 403
    assert db.query(Sesion).count() == 0
    # con sesión
    entrar_tutor(c, "8341445807", "temporal123")
    assert c.post("/salir", data={}).status_code == 403
    assert c.post("/cambiar-contrasena", data={
        "actual": "temporal123", "nueva": "nueva-clave-1", "confirmar": "nueva-clave-1",
    }).status_code == 403
    assert db.query(Sesion).count() == 1


def test_cookie_httponly_secure_samesite(db, op, c):
    crear_tutor(db, op)
    pagina = c.get("/entrar")
    csrf_cookie = pagina.headers["set-cookie"].lower()
    assert "sb_csrf_login=" in csrf_cookie
    for bandera in ("httponly", "secure", "samesite=lax"):
        assert bandera in csrf_cookie
    r = c.post("/entrar", data={"usuario": "8341445807", "contrasena": "temporal123",
                                "csrf": csrf_de(pagina)})
    cookies = [h.lower() for h in r.headers.get_list("set-cookie")]
    ses = next(h for h in cookies if h.startswith("sb_sesion="))
    for bandera in ("httponly", "secure", "samesite=lax"):
        assert bandera in ses
    assert any(h.startswith("sb_csrf_login=") and "max-age=0" in h for h in cookies)


def test_login_rota_la_sesion(db, op, c):
    crear_tutor(db, op, debe_cambiar=False)
    entrar_tutor(c, "8341445807", "temporal123")
    vieja = c.cookies.get("sb_sesion")
    entrar_tutor(c, "8341445807", "temporal123")
    assert c.cookies.get("sb_sesion") != vieja
    assert db.query(Sesion).count() == 1


def test_salir_borra_sesion(db, op, c):
    crear_tutor(db, op, debe_cambiar=False)
    entrar_tutor(c, "8341445807", "temporal123")
    pagina = c.get("/")
    r = c.post("/salir", data={"csrf": csrf_de(pagina)})
    assert r.status_code == 303 and r.headers["location"] == "/entrar"
    assert db.query(Sesion).count() == 0
    assert c.get("/").headers["location"] == "/entrar"


def test_host_desconocido_404(op, app):
    otro = TestClient(app, base_url="https://otro.test", follow_redirects=False)
    assert otro.get("/entrar").status_code == 404
    assert otro.post("/entrar", data={}).status_code == 404
