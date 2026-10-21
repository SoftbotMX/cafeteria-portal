import re

import pytest
from fastapi.testclient import TestClient

from portal.models import Bitacora, IntentoLogin, Tutor
from portal.seguridad import verificar_contrasena
from tests.conduit_falso import ConduitFalso
from tests.fabricas import crear_admin, crear_operador, crear_tutor, csrf_de, entrar_tutor
from tests.tpv_falso import TPVFalso

HIJOS = {"hijo_nombre": ["Luis Pérez", "Sofía Pérez", "", "", ""],
         "hijo_matricula": ["A-1", "", "", "", ""]}


@pytest.fixture
def op(db):
    return crear_operador(db)


@pytest.fixture
def tpv(app):
    f = TPVFalso(secreto_admin="secreto-admin")
    app.state.tpv_transport = f.transport
    return f


@pytest.fixture
def conduit(app):
    f = ConduitFalso(secreto="secreto-conduit")
    app.state.conduit_transport = f.transport
    return f


@pytest.fixture
def c(app):
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


def entrar_admin(c, usuario="caja", contrasena="admin1234"):
    pagina = c.get("/admin/entrar")
    return c.post("/admin/entrar", data={"usuario": usuario, "contrasena": contrasena,
                                         "csrf": csrf_de(pagina)})


def _llave(resp) -> str:
    return re.search(r'name="llave" value="([^"]*)"', resp.text).group(1)


def datos_alta(pagina, **kw):
    d = {"nombre": "María Pérez", "telefono": "834 144 5807", "correo": "", "csrf": csrf_de(pagina),
         "llave": _llave(pagina), **HIJOS}
    d.update(kw)
    return d


@pytest.fixture
def admin(db, op, c):
    crear_admin(db, op)
    assert entrar_admin(c).status_code == 303
    return c


def _contrasena(resp) -> str:
    return re.search(r'id="contrasena"[^>]*>([^<]+)<', resp.text).group(1).strip()


def _escrituras_tpv(db):
    return db.query(Bitacora).filter(Bitacora.operacion.like("admin/%")).count()


# ---- acceso ----

def test_admin_entra(db, op, c):
    crear_admin(db, op)
    r = entrar_admin(c)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert "sb_sesion" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    crear_tutor(db, op, nombre="Ana López")
    pagina = c.get("/admin")
    assert pagina.status_code == 200 and "Ana López" in pagina.text


def test_admin_contrasena_mala_y_limite(db, op, c):
    crear_admin(db, op)
    for _ in range(5):
        r = entrar_admin(c, "caja", "mala")
        assert r.status_code == 200 and "Usuario o contraseña incorrectos" in r.text
    assert entrar_admin(c, "caja", "admin1234").status_code == 429
    assert db.query(IntentoLogin).filter_by(exito=False).count() == 5
    assert all(i.usuario.startswith("admin:") for i in db.query(IntentoLogin).all())


def test_admin_usuario_inexistente_mismo_mensaje(db, op, c):
    crear_admin(db, op)
    r = entrar_admin(c, "nadie", "lo-que-sea")
    assert r.status_code == 200 and "Usuario o contraseña incorrectos" in r.text


def test_admin_login_sin_csrf_403(db, op, c):
    crear_admin(db, op)
    r = c.post("/admin/entrar", data={"usuario": "caja", "contrasena": "admin1234", "csrf": "x"})
    assert r.status_code == 403 and "Tu sesión expiró, vuelve a intentarlo." in r.text


def test_admin_sin_sesion_va_al_login(c, op):
    for ruta in ("/admin", "/admin/alta"):
        r = c.get(ruta)
        assert r.status_code == 303 and r.headers["location"] == "/admin/entrar"


def test_admin_salir(admin):
    pagina = admin.get("/admin")
    r = admin.post("/admin/salir", data={"csrf": csrf_de(pagina)})
    assert r.status_code == 303 and r.headers["location"] == "/admin/entrar"
    assert admin.get("/admin").status_code == 303


def test_tutor_no_entra_al_admin(db, op, c):
    crear_tutor(db, op, debe_cambiar=False)
    assert entrar_tutor(c, "8341445807", "temporal123").status_code == 303
    for ruta in ("/admin", "/admin/alta"):
        r = c.get(ruta)
        assert r.status_code == 303 and r.headers["location"] == "/admin/entrar"
    assert c.post("/admin/alta", data={"nombre": "x"}).status_code == 303


# ---- alta ----

def test_alta_crea_tutor_e_hijos_en_tpv(db, op, admin, tpv, conduit):
    pagina = admin.get("/admin/alta")
    r = admin.post("/admin/alta", data=datos_alta(pagina))
    assert r.status_code == 200
    assert tpv.llamadas == {"admin/tutor/crear": 1, "admin/alumno/crear": 2}
    t = db.query(Tutor).one()
    assert t.telefono == "8341445807" and t.nombre == "María Pérez" and t.debe_cambiar
    assert all(a["nombre"] for a in tpv.alumnos.values())
    for _, cuerpo, _ in (v for v in tpv.guardado.values() if v[0] == "admin/alumno/crear"):
        assert cuerpo["tutores"] == [t.tutor_ref]
    assert _escrituras_tpv(db) == 3
    assert "C0001" in r.text and "C0002" in r.text and "Luis Pérez" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert "matricula" not in [v[1] for v in tpv.guardado.values() if v[1]["nombre"] == "Sofía Pérez"][0]


def test_alta_guarda_hash_de_la_contrasena_mostrada(db, op, admin, tpv, conduit):
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    t = db.query(Tutor).one()
    assert verificar_contrasena(t.contrasena_hash, _contrasena(r)) and t.debe_cambiar


def test_alta_envia_por_whatsapp(db, op, admin, tpv, conduit):
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    assert len(conduit.enviados) == 1
    env = conduit.enviados[0]
    pw = _contrasena(r)
    assert env["recipient"] == "528341445807" and pw in env["text"]
    t = db.query(Tutor).one()
    assert env["idempotency_key"] == f"acceso:{t.id}:1"
    assert "https://cafeteria.test" in env["text"] and "8341445807" in env["text"]
    assert "whatsapp" in r.text.lower() and "enviado" in r.text.lower()


def test_alta_sin_telefono_ni_smtp_queda_sin_canal_y_muestra_contrasena(db, op, admin, tpv, conduit):
    r = admin.post("/admin/alta", data=datos_alta(
        admin.get("/admin/alta"), telefono="", correo="  Mama@Mail.COM "))
    assert r.status_code == 200 and conduit.enviados == []
    assert "sin_canal" in r.text or "Sin canal" in r.text
    t = db.query(Tutor).one()
    assert t.correo == "mama@mail.com" and t.telefono is None
    assert verificar_contrasena(t.contrasena_hash, _contrasena(r))


def test_alta_error_de_conduit_muestra_error_y_contrasena(db, op, admin, tpv, conduit):
    conduit.responder = 500
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    assert r.status_code == 200 and "error" in r.text.lower()
    assert verificar_contrasena(db.query(Tutor).one().contrasena_hash, _contrasena(r))


def test_alta_doble_clic_no_duplica(db, op, admin, tpv, conduit):
    pagina = admin.get("/admin/alta")
    datos = datos_alta(pagina)
    r1 = admin.post("/admin/alta", data=datos)
    h1 = db.query(Tutor).one().contrasena_hash
    r2 = admin.post("/admin/alta", data=datos)
    assert db.query(Tutor).count() == 1
    assert tpv.llamadas == {"admin/tutor/crear": 1, "admin/alumno/crear": 2}
    assert len(conduit.enviados) == 1
    assert r2.status_code == 200 and "Este alta ya se completó" in r2.text
    assert _contrasena(r1) not in r2.text and 'id="contrasena"' not in r2.text
    db.expire_all()
    assert db.query(Tutor).one().contrasena_hash == h1


def test_alta_que_falla_a_medias_se_completa_al_reintentar(db, op, admin, tpv, conduit):
    pagina = admin.get("/admin/alta")
    datos = datos_alta(pagina)
    tpv.fallar_en = {"admin/alumno/crear"}
    r = admin.post("/admin/alta", data=datos)
    assert r.status_code >= 400 and "TPV" in r.text
    assert _llave(r) == datos["llave"] and "María Pérez" in r.text and "Luis Pérez" in r.text
    assert 'id="contrasena"' not in r.text and conduit.enviados == []
    t = db.query(Tutor).one()
    assert t.alta_llave == datos["llave"]
    assert db.query(Bitacora).filter_by(http_status=503).count() == 1
    tpv.fallar_en = set()
    datos["csrf"] = csrf_de(r)
    r2 = admin.post("/admin/alta", data=datos)
    assert r2.status_code == 200 and 'id="contrasena"' in r2.text
    assert db.query(Tutor).count() == 1 and len(tpv.alumnos) == 2
    assert len(tpv.tutores) == 1 and len(conduit.enviados) == 1
    assert "C0001" in r2.text and "C0002" in r2.text


def test_alta_sin_conexion_al_tpv_conserva_formulario(db, op, admin, tpv):
    tpv.fallar_en = {"admin/tutor/crear"}
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    assert r.status_code >= 400 and "María Pérez" in r.text
    assert db.query(Tutor).count() == 0
    assert db.query(Bitacora).filter_by(operacion="admin/tutor/crear", http_status=503).count() == 1


def test_alta_telefono_repetido_rechazada_sin_llamar_al_tpv(db, op, admin, tpv):
    crear_tutor(db, op, telefono="8341445807")
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta"), telefono="(834) 144-5807"))
    assert r.status_code == 400 and "Ya hay un tutor con ese teléfono" in r.text
    assert tpv.llamadas == {} and db.query(Tutor).count() == 1


def test_alta_correo_repetido_en_otra_capitalizacion(db, op, admin, tpv):
    crear_tutor(db, op, telefono=None, correo="ana@mail.com")
    r = admin.post("/admin/alta", data=datos_alta(
        admin.get("/admin/alta"), telefono="", correo="ANA@mail.com"))
    assert r.status_code == 400 and "Ya hay un tutor con ese correo" in r.text
    assert tpv.llamadas == {}


def test_alta_requiere_telefono_o_correo(db, op, admin, tpv):
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta"), telefono="", correo=""))
    assert r.status_code == 400 and "teléfono o un correo" in r.text and tpv.llamadas == {}


def test_alta_telefono_invalido_es_error_de_formulario(db, op, admin, tpv):
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta"), telefono="123"))
    assert r.status_code == 400 and "teléfono" in r.text and tpv.llamadas == {}


def test_alta_requiere_nombre_y_un_hijo(db, op, admin, tpv):
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta"), nombre="  "))
    assert r.status_code == 400
    r = admin.post("/admin/alta", data=datos_alta(
        admin.get("/admin/alta"), hijo_nombre=["", "", "", "", ""]))
    assert r.status_code == 400 and tpv.llamadas == {}


def test_alta_llave_invalida_no_llega_al_tpv(db, op, admin, tpv):
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta"), llave="x" * 300))
    assert r.status_code == 400 and tpv.llamadas == {}
    assert len(_llave(r)) < 100


def test_alta_sin_csrf_403(db, op, admin, tpv):
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta"), csrf="falso"))
    assert r.status_code == 403 and "Tu sesión expiró, vuelve a intentarlo." in r.text
    assert tpv.llamadas == {} and db.query(Tutor).count() == 0


def test_pagina_no_contiene_secretos(db, op, admin, tpv, conduit):
    paginas = [admin.get("/admin"), admin.get("/admin/alta"), admin.get("/admin/entrar")]
    paginas.append(admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta"))))
    for p in paginas:
        assert "secreto-admin" not in p.text and "secreto-conduit" not in p.text


def test_alta_y_primer_ingreso(db, op, admin, tpv, conduit, app):
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    pw = _contrasena(r)
    t = TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)
    ing = entrar_tutor(t, "8341445807", pw)
    assert ing.status_code == 303 and ing.headers["location"] == "/cambiar-contrasena"
    pag = t.get("/cambiar-contrasena")
    r2 = t.post("/cambiar-contrasena", data={"actual": pw, "nueva": "nueva-clave-1",
                                             "confirmar": "nueva-clave-1", "csrf": csrf_de(pag)})
    assert r2.status_code == 303
    assert "María Pérez" in t.get("/").text


def test_alta_reentrega_cierra_sesiones_previas_del_tutor(db, op, admin, tpv, conduit, app):
    # reintento tras una falla: si el tutor ya tenía sesión, la contraseña vieja deja de valer
    from portal.credenciales import entregar_credenciales
    from portal.sesiones import crear_sesion
    from portal.models import Sesion
    t = crear_tutor(db, op, telefono="8340000001")
    crear_sesion(db, "tutor", t.id, op.id)
    db.commit()
    res = entregar_credenciales(db, app, op, t, 1)
    db.commit()
    assert db.query(Sesion).filter_by(tipo="tutor").count() == 0
    assert res.estado == "enviado" and res.canal == "whatsapp"
    assert "contrasena" not in repr(res)


def test_reintento_con_otro_telefono_se_rechaza(db, op, admin, tpv, conduit):
    datos = datos_alta(admin.get("/admin/alta"))
    tpv.fallar_en = {"admin/alumno/crear"}
    r = admin.post("/admin/alta", data=datos)
    tpv.fallar_en = set()
    datos.update(csrf=csrf_de(r), telefono="834 000 0002")
    r2 = admin.post("/admin/alta", data=datos)
    assert r2.status_code == 400 and "mismos datos del tutor" in r2.text
    assert conduit.enviados == [] and db.query(Tutor).one().telefono == "8341445807"


def test_admin_con_usuario_largo_puede_dar_de_alta(db, op, c, tpv, conduit):
    crear_admin(db, op, usuario="u" * 90)
    assert entrar_admin(c, "u" * 90).status_code == 303
    r = c.post("/admin/alta", data=datos_alta(c.get("/admin/alta")))
    assert r.status_code == 200 and db.query(Tutor).count() == 1


# ---- ronda de correcciones 1 ----

def _falla_en_hijo_2(admin, tpv):
    datos = datos_alta(admin.get("/admin/alta"))
    tpv.fallar_en = {"admin/alumno/crear"}
    r = admin.post("/admin/alta", data=datos)
    tpv.fallar_en = set()
    return datos, r


def test_falla_a_medias_vuelve_con_formulario_de_solo_lectura(db, op, admin, tpv):
    datos, r = _falla_en_hijo_2(admin, tpv)
    assert "Reintenta sin cambiar nada. Si algo está mal, termina el alta y corrígelo desde la ficha del tutor." in r.text
    assert len(re.findall(r"<input[^>]* readonly", r.text)) == 3 + 2 * 2  # tutor + 2 hijos
    assert r.text.count('name="hijo_nombre"') == 2  # sin filas vacías


def test_falla_antes_del_tutor_deja_el_formulario_editable(db, op, admin, tpv):
    tpv.fallar_en = {"admin/tutor/crear"}
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    assert "readonly" not in r.text and "Reintenta sin cambiar nada" not in r.text
    assert r.text.count('name="hijo_nombre"') == 5


def test_hijo_editado_tras_exito_parcial_no_duplica(db, op, admin, tpv, conduit):
    datos = datos_alta(admin.get("/admin/alta"))
    # TPV: el primer alumno entra y el segundo falla.
    original = tpv._operar

    def _operar(op_, d):
        if op_ == "admin/alumno/crear" and d["nombre"] == "Sofía Pérez" and tpv.fallar_hijo2:
            import httpx
            return httpx.Response(503, json={"ok": False, "error": {"codigo": "x", "mensaje": "x"}})
        return original(op_, d)

    tpv.fallar_hijo2 = True
    tpv._operar = _operar
    r = admin.post("/admin/alta", data=datos)
    assert r.status_code == 400 and len(tpv.alumnos) == 1
    tpv.fallar_hijo2 = False
    datos.update(csrf=csrf_de(r))
    datos["hijo_nombre"] = ["Luis EDITADO", "Sofía Pérez", "", "", ""]
    r2 = admin.post("/admin/alta", data=datos)
    assert r2.status_code == 400
    assert "Los datos cambiaron desde el primer intento. Reintenta con los datos originales o corrige desde la ficha del tutor." in r2.text
    assert len(tpv.alumnos) == 1 and len(tpv.tutores) == 1 and db.query(Tutor).count() == 1
    assert conduit.enviados == []


def test_tpv_503_dice_no_se_pudo_contactar(db, op, admin, tpv):
    _, r = _falla_en_hijo_2(admin, tpv)
    assert "No se pudo contactar al TPV. Vuelve a intentar en un momento." in r.text


def test_lista_marca_alta_incompleta(db, op, admin, tpv, conduit):
    crear_tutor(db, op, telefono="8340000009", nombre="Sin alta web")
    _falla_en_hijo_2(admin, tpv)
    pagina = admin.get("/admin")
    assert pagina.text.count("Alta incompleta") == 1
    datos = datos_alta(admin.get("/admin/alta"))
    admin.post("/admin/alta", data=datos)  # alta nueva completa
    assert admin.get("/admin").text.count("Alta incompleta") == 1


def test_carrera_de_telefono_no_pierde_la_bitacora_del_tpv(db, op, admin, tpv, monkeypatch):
    import portal.alta as alta
    crear_tutor(db, op, telefono="8341445807")
    monkeypatch.setattr(alta, "_choque", lambda *a, **k: None)
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    assert r.status_code == 400
    assert db.query(Bitacora).filter_by(operacion="admin/tutor/crear", http_status=200).count() == 1


def test_llave_de_cifrado_mala_no_deja_acceso_entregado(db, op, admin, tpv, conduit):
    from cryptography.fernet import Fernet
    from portal.cifrado import cifrar
    op.conduit_secreto_cif = cifrar("x", Fernet.generate_key().decode())
    db.commit()
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    assert r.status_code == 500 and "configuración de WhatsApp" in r.text
    assert 'id="contrasena"' not in r.text
    t = db.query(Tutor).one()  # el tutor sobrevive
    assert t.contrasena_hash == "!"
    assert db.query(Bitacora).filter_by(operacion="entregar_acceso").count() == 0


def test_alta_completa_muestra_estado_guardado_del_envio(db, op, admin, tpv, conduit):
    conduit.responder = 500
    datos = datos_alta(admin.get("/admin/alta"))
    admin.post("/admin/alta", data=datos)
    r = admin.post("/admin/alta", data=datos)
    assert 'id="envio">error<' in r.text
