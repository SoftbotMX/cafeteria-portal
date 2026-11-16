"""Pagos con tarjeta desde la administración: pausar/activar las recargas en línea (web, bot y
asistente) y cambiar la cuenta de Mercado Pago."""
import pytest

from portal import acciones
from portal.cifrado import cifrar, descifrar
from portal.models import IntentoLogin, Operador
from tests.conftest import FERNET_PRUEBAS
from tests.fabricas import crear_admin, crear_tutor, csrf_de, entrar_tutor
from tests.mp_falso import MPFalso
from tests.test_admin_alta import c, entrar_admin  # noqa: F401  fixtures compartidas
from tests.tpv_falso import TPVFalso

TOKEN_PRUEBA = "TEST-1234567890-viejo"
TOKEN_REAL = "APP_USR-9876543210-real"


@pytest.fixture
def op(db):
    from tests.fabricas import crear_operador
    return crear_operador(db, mp_access_token_cif=cifrar(TOKEN_PRUEBA, FERNET_PRUEBAS))


@pytest.fixture
def mp(app):
    f = MPFalso(token=TOKEN_PRUEBA)
    f.cuentas[TOKEN_PRUEBA] = {"id": 111, "nickname": "TESTSOFTBOT", "email": "dev@softbot.test",
                               "site_id": "MLM", "tags": ["normal"]}
    f.cuentas[TOKEN_REAL] = {"id": 222, "nickname": "CAFETERIA_REAL", "email": "caja@cafeteria.mx",
                             "site_id": "MLM", "tags": ["normal"]}
    app.state.mp_transport = f.transport
    return f


@pytest.fixture
def admin(db, op, c):  # noqa: F811
    crear_admin(db, op)
    assert entrar_admin(c).status_code == 303
    return c


def _op(db, op) -> Operador:
    db.expire_all()
    return db.get(Operador, op.id)


def _estado(admin, activo):
    p = admin.get("/admin/pagos")
    return admin.post("/admin/pagos/estado", data={"csrf": csrf_de(p), "activo": activo})


def _credenciales(admin, token=TOKEN_REAL, contrasena="admin1234"):
    p = admin.get("/admin/pagos")
    return admin.post("/admin/pagos/credenciales", data={
        "csrf": csrf_de(p), "access_token": token, "contrasena": contrasena})


def test_pantalla_en_el_menu(admin):
    r = admin.get("/admin/pagos")
    assert r.status_code == 200 and "Pagos con" in r.text and "Recargas en línea" in r.text
    assert 'href="/admin/pagos"' in admin.get("/admin").text


def test_pausar_quita_la_recarga_de_la_web_el_bot_y_el_asistente(db, op, admin, app, mp):
    r = _estado(admin, "0")
    assert r.status_code == 200 and "en pausa" in r.text
    o = _op(db, op)
    assert o.mp_pagos_activos is False and not o.recargas_activas
    assert o.mp_access_token_cif, "pausar no borra la credencial: el barrido la sigue usando"
    # Web: el padre de familia ya no ve ni puede abrir «Recargar».
    tpv = TPVFalso(secreto_tutor="secreto-tutor")
    app.state.tpv_transport = tpv.transport
    tutor = crear_tutor(db, o, debe_cambiar=False)
    tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez")
    tpv.agregar_hijo(tutor.tutor_ref, "Ana Pérez")  # con uno solo, el inicio lleva a su ficha
    from fastapi.testclient import TestClient
    papa = TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)
    assert entrar_tutor(papa, "8341445807", "temporal123").status_code == 303
    r = papa.get("/")
    assert r.status_code == 200 and "Luis" in r.text and 'href="/recargar"' not in r.text
    assert papa.get("/recargar").status_code == 404
    # Bot y asistente: la misma acción compartida dice que no está disponible.
    res = acciones.recargar(db, app, o, tutor, [], 0, "llave-pausa")
    assert res.estado == "rechazo" and "no están disponibles" in res.texto
    # Activar las devuelve.
    assert _estado(admin, "1").status_code == 200
    assert _op(db, op).recargas_activas
    r = papa.get("/")
    assert r.status_code == 200, r.headers.get("location")
    assert 'href="/recargar"' in r.text


def test_no_se_activa_sin_credenciales(db, op, admin):
    o = _op(db, op)
    o.mp_access_token_cif = None
    db.commit()
    r = _estado(admin, "1")
    assert r.status_code == 400 and "Primero guarda las credenciales" in r.text


def test_cambiar_credenciales_exige_la_pausa(db, op, admin, mp):
    r = _credenciales(admin)
    assert r.status_code == 409 and "Pausa los pagos" in r.text
    assert descifrar(_op(db, op).mp_access_token_cif, FERNET_PRUEBAS) == TOKEN_PRUEBA


def test_cambiar_credenciales_a_la_cuenta_real(db, op, admin, mp):
    _estado(admin, "0")
    r = _credenciales(admin)
    assert r.status_code == 200 and "cuenta real CAFETERIA_REAL" in r.text
    assert TOKEN_REAL not in r.text, "el token nunca se devuelve en la página"
    o = _op(db, op)
    assert descifrar(o.mp_access_token_cif, FERNET_PRUEBAS) == TOKEN_REAL
    assert (o.mp_cuenta_id, o.mp_cuenta_prueba) == ("222", False)
    assert "CAFETERIA_REAL" in o.mp_cuenta_nombre
    assert o.mp_pagos_activos is False, "se queda en pausa hasta que la administración la active"
    assert "Real · cobra dinero real" in admin.get("/admin/pagos").text


def test_token_de_prueba_se_marca_de_prueba(db, op, admin, mp):
    mp.cuentas["TEST-55555555555-otro"] = {"id": 333, "nickname": "X", "site_id": "MLM", "tags": []}
    _estado(admin, "0")
    assert _credenciales(admin, token="TEST-55555555555-otro").status_code == 200
    assert _op(db, op).mp_cuenta_prueba is True
    mp.cuentas["APP_USR-4444444444-usuario-de-prueba"] = {
        "id": 444, "nickname": "TESTUSER1", "site_id": "MLM", "tags": ["test_user"]}
    assert _credenciales(admin, token="APP_USR-4444444444-usuario-de-prueba").status_code == 200
    assert _op(db, op).mp_cuenta_prueba is True


@pytest.mark.parametrize("token,mensaje", [
    ("hola", "empieza con APP_USR- o TEST-"),
    ("APP_USR-0000000000-desconocido", "no reconoció esa credencial"),
])
def test_credencial_invalida_no_cambia_nada(db, op, admin, mp, token, mensaje):
    _estado(admin, "0")
    r = _credenciales(admin, token=token)
    assert r.status_code == 400 and mensaje in r.text
    assert descifrar(_op(db, op).mp_access_token_cif, FERNET_PRUEBAS) == TOKEN_PRUEBA


def test_cuenta_de_otro_pais_no_se_guarda(db, op, admin, mp):
    mp.cuentas["APP_USR-7777777777-argentina"] = {"id": 7, "site_id": "MLA", "tags": []}
    _estado(admin, "0")
    r = _credenciales(admin, token="APP_USR-7777777777-argentina")
    assert r.status_code == 400 and "no es de México" in r.text
    assert descifrar(_op(db, op).mp_access_token_cif, FERNET_PRUEBAS) == TOKEN_PRUEBA


def test_pide_la_contrasena_del_administrador(db, op, admin, mp):
    _estado(admin, "0")
    r = _credenciales(admin, contrasena="otra")
    assert r.status_code == 400 and "contraseña del administrador no es correcta" in r.text
    assert descifrar(_op(db, op).mp_access_token_cif, FERNET_PRUEBAS) == TOKEN_PRUEBA
    assert db.query(IntentoLogin).filter(IntentoLogin.exito.is_(False)).count() == 1, \
        "cuenta en el límite de intentos, como entrar"


def test_exige_csrf_y_sesion_de_admin(db, op, admin, mp, app):
    _estado(admin, "0")
    r = admin.post("/admin/pagos/credenciales", data={
        "csrf": "x", "access_token": TOKEN_REAL, "contrasena": "admin1234"})
    assert r.status_code == 403
    from fastapi.testclient import TestClient
    anonimo = TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)
    assert anonimo.get("/admin/pagos").status_code in (303, 401, 403)
    assert anonimo.post("/admin/pagos/estado", data={"activo": "1"}).status_code in (303, 401, 403)


def test_verificar_la_cuenta_guardada(db, op, admin, mp):
    r = admin.post("/admin/pagos/verificar", data={"csrf": csrf_de(admin.get("/admin/pagos"))})
    assert r.status_code == 200 and "Datos de la cuenta consultados" in r.text
    assert "Consultar datos de la cuenta guardada" not in r.text, "ya consultada, el botón sobra"
    o = _op(db, op)
    assert (o.mp_cuenta_id, o.mp_cuenta_prueba) == ("111", True)


def _recarga(db, op, **kw):
    from datetime import datetime, timedelta, timezone
    from portal.models import Recarga, Tutor
    tutor = db.query(Tutor).filter_by(operador_id=op.id).first() or crear_tutor(db, op)
    datos = dict(operador_id=op.id, tutor_id=tutor.id, llave=f"ll-{len(kw)}-{id(kw)}", total=100,
                 reparto=[], estado="pendiente", init_point="https://mp.test/x",
                 liga_vence=datetime.now(timezone.utc) + timedelta(minutes=20))
    datos.update(kw)
    r = Recarga(**datos)
    db.add(r)
    db.commit()
    return r


def test_no_cambia_de_cuenta_con_recargas_en_curso(db, op, admin, mp):
    """Con otra cuenta, el pago de una liga ya abierta no se podría consultar (Codex)."""
    _estado(admin, "0")
    r = _recarga(db, op)
    pagina = admin.get("/admin/pagos")
    assert "Todavía hay recargas" in pagina.text
    assert '<fieldset class="sin-marco" disabled>' in pagina.text, "el formulario se ve, deshabilitado"
    res = _credenciales(admin)
    assert res.status_code == 409 and "Todavía hay recargas" in res.text
    assert descifrar(_op(db, op).mp_access_token_cif, FERNET_PRUEBAS) == TOKEN_PRUEBA
    # Acreditada pero sin su revisión final de pagos dobles: también espera.
    r.estado, r.conciliada = "acreditada", False
    db.commit()
    assert _credenciales(admin).status_code == 409
    from datetime import datetime, timedelta, timezone
    r.conciliada = True
    r.liga_vence = datetime.now(timezone.utc) - timedelta(minutes=1)  # su liga ya no se puede pagar
    db.commit()
    assert _credenciales(admin).status_code == 200


def test_crear_no_abre_liga_si_se_pauso_a_medio_camino(db, op, app, mp):
    """La pausa llega después de que la ruta revisó el permiso: la liga no se abre (Codex)."""
    from portal import recargas
    from portal.models import Recarga
    tutor = crear_tutor(db, op)
    o = _op(db, op)
    assert o.recargas_activas
    db.execute(Operador.__table__.update().where(Operador.id == op.id).values(mp_pagos_activos=False))
    db.commit()
    with pytest.raises(recargas.ErrorMP):
        recargas.crear(db, app, o, tutor, [], 100, "llave-carrera", "https://cafeteria.test")
    assert not mp.preferencias
    assert db.query(Recarga).filter_by(llave="llave-carrera").one().init_point is None


def test_crear_no_repite_una_liga_existente_en_pausa(db, op, app, mp):
    """Reenviar la confirmación con los pagos en pausa no devuelve la liga ya creada (Codex)."""
    from portal import recargas
    r = _recarga(db, op, llave="llave-repetida")
    tutor_id = r.tutor_id
    from portal.models import Tutor
    tutor = db.get(Tutor, tutor_id)
    db.execute(Operador.__table__.update().where(Operador.id == op.id).values(mp_pagos_activos=False))
    db.commit()
    with pytest.raises(recargas.ErrorMP):
        recargas.crear(db, app, _op(db, op), tutor, [], 100, "llave-repetida", "https://cafeteria.test")


def test_una_recarga_vencida_no_estrena_liga(db, op, app, mp):
    """Reenviar la confirmación de una recarga que venció sin liga no abre una nueva (Codex)."""
    from portal import recargas
    from portal.models import Tutor
    r = _recarga(db, op, llave="llave-vencida", estado="vencida", init_point=None, liga_vence=None)
    tutor = db.get(Tutor, r.tutor_id)
    with pytest.raises(recargas.ErrorMP):
        recargas.crear(db, app, _op(db, op), tutor, [], 100, "llave-vencida", "https://cafeteria.test")
    assert not mp.preferencias


def test_liga_vigente_de_cualquier_estado_bloquea_el_cambio(db, op, admin, mp):
    _estado(admin, "0")
    _recarga(db, op, estado="vencida")  # liga viva aunque el estado diga vencida
    assert _credenciales(admin).status_code == 409


def test_no_abre_liga_que_venceria_despues_de_las_24_horas(db, op, app, mp):
    """Una liga abierta a las 23 h 50 min seguiría viva cuando el barrido ya dio la recarga por
    vencida; su pago no lo buscaría nadie (Codex)."""
    from datetime import datetime, timedelta, timezone
    from portal import recargas
    from portal.models import Tutor
    r = _recarga(db, op, llave="llave-tarde", init_point=None, liga_vence=None,
                 creada=datetime.now(timezone.utc) - timedelta(hours=23, minutes=50))
    tutor = db.get(Tutor, r.tutor_id)
    with pytest.raises(recargas.ErrorMP):
        recargas.crear(db, app, _op(db, op), tutor, [], 100, "llave-tarde", "https://cafeteria.test")
    assert not mp.preferencias


def test_las_confirmaciones_usan_el_dialogo_del_portal(admin):
    """Nada del confirm() del navegador: el diálogo propio de la administración (data-confirmar)."""
    from pathlib import Path
    plantillas = Path(__file__).parent.parent / "portal" / "plantillas"
    for archivo in plantillas.rglob("*.html"):
        assert "confirm(" not in archivo.read_text(), archivo.name
    pagina = admin.get("/admin/pagos").text
    assert 'id="confirmar"' in pagina and "confirmar.js" in pagina
    assert 'data-confirmar="¿Pausar los pagos con tarjeta?"' in pagina
