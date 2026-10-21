import tomllib
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portal import seguridad
from portal.app import create_app
from portal.config import Settings
from portal.models import IntentoLogin
from portal.seguridad import limite_excedido, normalizar_telefono, verificar_contrasena
from tests.conftest import FERNET_PRUEBAS, URL_PRUEBAS
from tests.fabricas import crear_admin, crear_operador, crear_tutor, csrf_de, entrar_tutor

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def op(db):
    return crear_operador(db)


@pytest.fixture
def c(app):
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


def _fallos(db, op, n, ip="testclient", prefijo="", base="u"):
    for i in range(n):
        db.add(IntentoLogin(operador_id=op.id, usuario=f"{prefijo}{base}{i}", ip=ip, exito=False,
                            creado=datetime.now(timezone.utc)))
    db.commit()


def _telefono(i: int) -> str:
    return f"55{i:08d}"


def test_25_solicitudes_de_codigo_no_bloquean_el_login_de_la_ip(db, op, c):
    crear_tutor(db, op)
    for i in range(25):
        pagina = c.get("/recuperar")
        r = c.post("/recuperar", data={"usuario": _telefono(i), "csrf": csrf_de(pagina)})
        assert r.status_code == 303
    r = entrar_tutor(c, "8341445807", "temporal123")
    assert r.status_code == 303  # entra: no hay 429


def test_solicitudes_de_codigo_tienen_su_tope_por_ip(db, op, c):
    for i in range(30):
        pagina = c.get("/recuperar")
        assert c.post("/recuperar", data={"usuario": _telefono(i), "csrf": csrf_de(pagina)}).status_code == 303
    pagina = c.get("/recuperar")
    r = c.post("/recuperar", data={"usuario": _telefono(99), "csrf": csrf_de(pagina)})
    assert r.status_code == 429


def test_codigos_incorrectos_si_cuentan_contra_la_ip(db, op, c):
    for i in range(50):
        pagina = c.get("/recuperar/codigo")
        r = c.post("/recuperar/codigo", data={
            "usuario": _telefono(i), "codigo": "000000", "nueva": "nueva-clave-1",
            "confirmar": "nueva-clave-1", "csrf": csrf_de(pagina)})
        assert r.status_code == 400
    assert entrar_tutor(c, "8341445807", "x").status_code == 429


def test_20_fallos_de_tutores_no_bloquean_el_login_admin(db, op):
    _fallos(db, op, 20)
    assert limite_excedido(db, op.id, "admin:caja", "testclient") is False
    assert limite_excedido(db, op.id, "8341445807", "testclient") is False


def test_50_fallos_de_tutores_bloquean_al_tutor(db, op):
    _fallos(db, op, 49)
    assert limite_excedido(db, op.id, "8341445807", "testclient") is False
    _fallos(db, op, 1, base="z")
    assert limite_excedido(db, op.id, "8341445807", "testclient") is True


def test_20_fallos_de_admin_bloquean_solo_al_admin(db, op):
    _fallos(db, op, 20, prefijo="admin:")
    assert limite_excedido(db, op.id, "admin:otro", "testclient") is True
    assert limite_excedido(db, op.id, "8341445807", "testclient") is False


def test_fallos_de_admin_y_solicitudes_no_cuentan_en_el_contador_de_tutores(db, op):
    _fallos(db, op, 30, prefijo="admin:")
    _fallos(db, op, 30, prefijo="recuperar:")
    assert limite_excedido(db, op.id, "8341445807", "testclient") is False


def test_login_admin_bloqueado_por_ip_aunque_cambie_de_usuario(db, op, c):
    crear_admin(db, op)
    _fallos(db, op, 20, prefijo="admin:")
    pagina = c.get("/admin/entrar")
    r = c.post("/admin/entrar", data={"usuario": "caja", "contrasena": "admin1234",
                                      "csrf": csrf_de(pagina)})
    assert r.status_code == 429


def test_hash_invalido_cuesta_lo_mismo_que_uno_real(monkeypatch):
    llamadas = []
    real = seguridad._hasher

    class Espia:
        def verify(self, h, c):
            llamadas.append(h)
            return real.verify(h, c)

    monkeypatch.setattr(seguridad, "_hasher", Espia())
    assert verificar_contrasena("!", "x") is False
    assert llamadas == [seguridad.HASH_FALSO]


def test_cookie_csrf_login_sin_prefijo_en_local(engine, op):
    app = create_app(Settings(database_url=URL_PRUEBAS, fernet_key=FERNET_PRUEBAS, cookie_secure=False))
    try:
        r = TestClient(app, base_url="http://cafeteria.test").get("/entrar")
        assert any(h.startswith("sb_csrf_login=") for h in r.headers.get_list("set-cookie"))
    finally:
        app.state.engine.dispose()


def test_instancia_por_defecto():
    assert Settings(database_url="x", fernet_key="y").instancia == "portal"


def test_telefono_con_un_digito_no_ascii_se_rechaza():
    assert normalizar_telefono("834144580７") is None
    assert normalizar_telefono("８３４１４４５８０７") is None
    assert normalizar_telefono("834 144 5807") == "8341445807"


def test_package_data_cubre_todas_las_plantillas():
    cfg = tomllib.loads((RAIZ / "pyproject.toml").read_text())
    globs = cfg["tool"]["setuptools"]["package-data"]["portal"]
    for carpeta in ("plantillas", "estatico"):
        for f in (RAIZ / "portal" / carpeta).rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                rel = f.relative_to(RAIZ / "portal").as_posix()
                assert any(_coincide(rel, g) for g in globs), rel


def _coincide(rel: str, patron: str) -> bool:
    # setuptools: "dir/**/*" incluye archivos a cualquier profundidad bajo dir/.
    if "**" in patron:
        base = patron.split("**")[0]
        return rel.startswith(base) and fnmatch(rel.rsplit("/", 1)[-1], patron.rsplit("/", 1)[-1])
    return fnmatch(rel, patron) and rel.count("/") == patron.count("/")


def test_usuario_con_dos_puntos_no_fabrica_prefijos(db, op, c):
    for u in ("recuperar:a@x.com", "admin:a@x.com", "cambio:1@x.com"):
        entrar_tutor(c, u, "mala-mala")
    usuarios = {i.usuario for i in db.query(IntentoLogin).all()}
    assert usuarios == {"invalido"}


def test_solicitudes_de_correo_largo_respetan_el_tope_de_3(db, op, c):
    correo = "a" * 240 + "@mail.com"  # 249 caracteres: con el prefijo, rebasa los 255 de la columna
    crear_tutor(db, op, telefono=None, correo=correo)
    codigos = []
    for _ in range(4):
        pagina = c.get("/recuperar")
        codigos.append(c.post("/recuperar", data={"usuario": correo, "csrf": csrf_de(pagina)}).status_code)
    assert codigos == [303, 303, 303, 429]


def test_login_concurrente_con_recuperacion_no_crea_sesion(db, op, c, monkeypatch, engine):
    from sqlalchemy.orm import Session
    from portal.models import Sesion, Tutor
    from portal.rutas import acceso
    t = crear_tutor(db, op)
    real = acceso.verificar_contrasena

    def verificar_y_cambiar(h, contrasena):
        ok = real(h, contrasena)
        # Entre verificar y crear la sesión, otra solicitud cambia la contraseña (recuperación).
        with Session(engine) as otra:
            otro = otra.get(Tutor, t.id)
            otro.contrasena_hash = seguridad.hash_contrasena("nueva-clave-1")
            otra.commit()
        return ok

    monkeypatch.setattr(acceso, "verificar_contrasena", verificar_y_cambiar)
    r = entrar_tutor(c, "8341445807", "temporal123")
    assert r.status_code == 200 and "Usuario o contraseña incorrectos" in r.text
    assert db.query(Sesion).count() == 0


def test_rafaga_de_logins_no_verifica_mas_de_5_contrasenas(db, op, app):
    from concurrent.futures import ThreadPoolExecutor
    crear_tutor(db, op)

    def intento(_):
        cl = TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)
        return entrar_tutor(cl, "8341445807", "mala-mala").status_code

    with ThreadPoolExecutor(8) as pool:
        codigos = list(pool.map(intento, range(8)))
    assert codigos.count(200) == 5 and codigos.count(429) == 3
