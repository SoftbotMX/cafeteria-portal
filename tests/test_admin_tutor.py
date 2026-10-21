import re

import pytest
from fastapi.testclient import TestClient

from portal.models import Bitacora, Sesion, Tutor
from portal.seguridad import verificar_contrasena
from tests.fabricas import crear_admin, crear_operador, crear_tutor, csrf_de, entrar_tutor
from tests.test_admin_alta import (  # noqa: F401  fixtures compartidas
    _contrasena, admin, c, conduit, datos_alta, entrar_admin, op, tpv,
)

_n = {"ref": 0}


def tutor_tpv(db, op, tpv, nombre="Ana López", hijos=(), telefono="8341445807", **kw):
    """Tutor local con su espejo en el TPV falso y los hijos indicados (ya vinculados)."""
    _n["ref"] += 1
    ref = f"R{_n['ref']}"
    tpv.tutores[ref] = {"tutor_ref": ref, "nombre": nombre, "telefono": telefono, "correo": None,
                        "activo": True, "alumnos": []}
    for h in hijos:
        aid = len(tpv.alumnos) + 1
        al = {"id": aid, "nombre": h, "matricula": None, "codigo": f"C{aid:04d}",
              "compra_sin_pedido": True, "disponible": 0, "retenido": 0, "credenciales": []}
        tpv.alumnos[aid] = al
        tpv.tutores[ref]["alumnos"].append(al)
    return crear_tutor(db, op, telefono=telefono, nombre=nombre, tutor_ref=ref, **kw)


def formulario(resp, accion, contiene=""):
    """Campos ocultos del formulario cuya acción es `accion` y cuyo HTML contiene `contiene`."""
    for act, cuerpo in re.findall(r'<form[^>]*action="([^"]+)"[^>]*>(.*?)</form>', resp.text, re.S):
        if act == accion and contiene in cuerpo:
            return dict(re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', cuerpo))
    raise AssertionError(f"no hay formulario {accion} con {contiene!r}")


def tutor_nuevo_cliente(app):
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


def test_ficha_muestra_hijos_con_codigo(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv, hijos=["Luis Pérez", "Sofía Pérez"])
    r = admin.get(f"/admin/tutores/{t.id}")
    assert r.status_code == 200
    assert "Luis Pérez" in r.text and "C0001" in r.text and "Sofía Pérez" in r.text and "C0002" in r.text
    assert "Ana López" in r.text and "8341445807" in r.text
    assert tpv.llamadas == {"admin/tutor/detalle": 1}
    assert r.headers["cache-control"] == "no-store"


def test_lista_enlaza_a_la_ficha(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv)
    assert f'href="/admin/tutores/{t.id}"' in admin.get("/admin").text


def test_ficha_de_otro_operador_404(db, op, admin, tpv):
    otro = crear_operador(db, host="otra.test")
    ajeno = crear_tutor(db, otro, telefono="8340000077")
    assert admin.get(f"/admin/tutores/{ajeno.id}").status_code == 404
    pagina = admin.get("/admin")
    datos = {"csrf": csrf_de(pagina), "llave": "11111111-aaaa", "alumno_id": 1, "nombre": "x",
             "otro_tutor_id": 1, "activo": "0"}
    for ruta in ("hijos", "vincular", "desvincular", "reenviar", "activo"):
        assert admin.post(f"/admin/tutores/{ajeno.id}/{ruta}", data=datos).status_code == 404
    assert tpv.llamadas == {}
    assert admin.get("/admin/tutores/9999").status_code == 404


def test_ficha_sin_sesion_va_al_login(db, op, c):
    t = crear_tutor(db, op)
    r = c.get(f"/admin/tutores/{t.id}")
    assert r.status_code == 303 and r.headers["location"] == "/admin/entrar"


def test_ficha_con_tpv_caido_muestra_aviso_y_acciones_locales(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv, hijos=["Luis Pérez"])
    tpv.fallar_en = {"admin/tutor/detalle"}
    r = admin.get(f"/admin/tutores/{t.id}")
    assert r.status_code == 200 and "No se pudo consultar el TPV" in r.text
    assert "Luis Pérez" not in r.text
    formulario(r, f"/admin/tutores/{t.id}/reenviar")
    formulario(r, f"/admin/tutores/{t.id}/activo")
    assert f"/admin/tutores/{t.id}/hijos" not in r.text


def test_agregar_hijo(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv)
    pagina = admin.get(f"/admin/tutores/{t.id}")
    campos = formulario(pagina, f"/admin/tutores/{t.id}/hijos")
    r = admin.post(f"/admin/tutores/{t.id}/hijos",
                   data={**campos, "nombre": " Mateo Ruiz ", "matricula": "B-7"})
    assert r.status_code == 200 and "Mateo Ruiz" in r.text and "C0001" in r.text
    (op_, cuerpo, _), = (v for v in tpv.guardado.values() if v[0] == "admin/alumno/crear")
    assert cuerpo["tutores"] == [t.tutor_ref] and cuerpo["nombre"] == "Mateo Ruiz"
    assert cuerpo["matricula"] == "B-7" and cuerpo["llave"] == campos["llave"]
    assert db.query(Bitacora).filter_by(operacion="admin/alumno/crear", http_status=200).count() == 1


def test_agregar_hijo_doble_clic_no_duplica(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv)
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/hijos")
    datos = {**campos, "nombre": "Mateo Ruiz", "matricula": ""}
    admin.post(f"/admin/tutores/{t.id}/hijos", data=datos)
    admin.post(f"/admin/tutores/{t.id}/hijos", data=datos)
    assert len(tpv.alumnos) == 1


def test_agregar_hijo_valida_y_no_llama_al_tpv(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv)
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/hijos")
    r = admin.post(f"/admin/tutores/{t.id}/hijos", data={**campos, "nombre": "  ", "matricula": ""})
    assert r.status_code == 400 and "nombre" in r.text.lower()
    r = admin.post(f"/admin/tutores/{t.id}/hijos", data={**campos, "nombre": "x", "matricula": "m" * 51})
    assert r.status_code == 400
    r = admin.post(f"/admin/tutores/{t.id}/hijos", data={**campos, "nombre": "x", "llave": "no valida!"})
    assert r.status_code == 400
    assert "admin/alumno/crear" not in tpv.llamadas


def test_agregar_hijo_error_del_tpv_usa_el_mensaje_de_alta(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv)
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/hijos")
    tpv.fallar_en = {"admin/alumno/crear"}
    r = admin.post(f"/admin/tutores/{t.id}/hijos", data={**campos, "nombre": "Mateo", "matricula": ""})
    assert r.status_code == 400
    assert "No se pudo contactar al TPV. Vuelve a intentar en un momento." in r.text
    assert db.query(Bitacora).filter_by(operacion="admin/alumno/crear", http_status=503).count() == 1
    # el formulario del reintento conserva llave y datos: no duplica si el TPV sí lo había creado
    retorno = formulario(r, f"/admin/tutores/{t.id}/hijos")
    assert retorno["llave"] == campos["llave"] and 'value="Mateo"' in r.text
    tpv.fallar_en = set()
    r2 = admin.post(f"/admin/tutores/{t.id}/hijos", data={**retorno, "nombre": "Mateo", "matricula": ""})
    assert r2.status_code == 200 and len(tpv.alumnos) == 1


def test_vincular_hijo_de_otro_tutor(db, op, admin, tpv):
    papa = tutor_tpv(db, op, tpv, nombre="Papá", hijos=["Luis Pérez"], telefono="8340000001")
    mama = tutor_tpv(db, op, tpv, nombre="Mamá", telefono="8340000002")
    pagina = admin.get(f"/admin/tutores/{mama.id}")
    assert f'value="{papa.id}"' in pagina.text  # el select ofrece al otro tutor
    paso2 = admin.get(f"/admin/tutores/{mama.id}?otro={papa.id}")
    assert "Luis Pérez" in paso2.text
    campos = formulario(paso2, f"/admin/tutores/{mama.id}/vincular")
    assert campos["otro_tutor_id"] == str(papa.id) and campos["alumno_id"] == "1"
    r = admin.post(f"/admin/tutores/{mama.id}/vincular", data=campos)
    assert r.status_code == 200 and "Luis Pérez" in r.text
    (_, cuerpo, _), = (v for v in tpv.guardado.values() if v[0] == "admin/vincular")
    assert cuerpo == {"tutor_ref": mama.tutor_ref, "alumno_id": 1, "activo": True,
                      "llave": campos["llave"]}
    assert [a["id"] for a in tpv.tutores[mama.tutor_ref]["alumnos"]] == [1]
    assert [a["id"] for a in tpv.tutores[papa.tutor_ref]["alumnos"]] == [1]
    assert db.query(Bitacora).filter_by(operacion="admin/vincular", http_status=200).count() == 1


def test_el_select_no_ofrece_al_propio_tutor_ni_a_otros_operadores(db, op, admin, tpv):
    mama = tutor_tpv(db, op, tpv, nombre="Mamá", telefono="8340000002")
    otro = crear_operador(db, host="otra.test")
    crear_tutor(db, otro, telefono="8340000099", nombre="Ajeno Total")
    r = admin.get(f"/admin/tutores/{mama.id}")
    assert "Ajeno Total" not in r.text
    assert f'<option value="{mama.id}"' not in r.text


def test_vincular_alumno_ajeno_404_sin_llamar_vincular(db, op, admin, tpv):
    papa = tutor_tpv(db, op, tpv, nombre="Papá", hijos=["Luis Pérez"], telefono="8340000001")
    otra = tutor_tpv(db, op, tpv, nombre="Otra", hijos=["Ajeno Chico"], telefono="8340000003")
    mama = tutor_tpv(db, op, tpv, nombre="Mamá", telefono="8340000002")
    campos = formulario(admin.get(f"/admin/tutores/{mama.id}?otro={papa.id}"),
                        f"/admin/tutores/{mama.id}/vincular")
    # alumno 2 es de «Otra», no de «Papá»
    r = admin.post(f"/admin/tutores/{mama.id}/vincular", data={**campos, "alumno_id": 2})
    assert r.status_code == 404
    assert "admin/vincular" not in tpv.llamadas
    # otro tutor de otro operador
    ope2 = crear_operador(db, host="otra.test")
    ajeno = crear_tutor(db, ope2, telefono="8340000088")
    r = admin.post(f"/admin/tutores/{mama.id}/vincular",
                   data={**campos, "otro_tutor_id": ajeno.id})
    assert r.status_code == 404
    # el mismo tutor como «otro»
    r = admin.post(f"/admin/tutores/{mama.id}/vincular", data={**campos, "otro_tutor_id": mama.id})
    assert r.status_code == 404
    assert "admin/vincular" not in tpv.llamadas


def test_vincular_con_tpv_caido_no_es_404(db, op, admin, tpv):
    papa = tutor_tpv(db, op, tpv, nombre="Papá", hijos=["Luis Pérez"], telefono="8340000001")
    mama = tutor_tpv(db, op, tpv, nombre="Mamá", telefono="8340000002")
    campos = formulario(admin.get(f"/admin/tutores/{mama.id}?otro={papa.id}"),
                        f"/admin/tutores/{mama.id}/vincular")
    tpv.fallar_en = {"admin/tutor/detalle"}
    r = admin.post(f"/admin/tutores/{mama.id}/vincular", data=campos)
    assert r.status_code == 400 and "TPV" in r.text
    assert "admin/vincular" not in tpv.llamadas


def test_desvincular(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv, hijos=["Luis Pérez", "Sofía Pérez"])
    pagina = admin.get(f"/admin/tutores/{t.id}")
    campos = formulario(pagina, f"/admin/tutores/{t.id}/desvincular", 'name="alumno_id" value="1"')
    r = admin.post(f"/admin/tutores/{t.id}/desvincular", data=campos)
    assert r.status_code == 200 and "ya no está vinculado" in r.text
    assert 'name="alumno_id" value="1"' not in r.text and "Sofía Pérez" in r.text
    (_, cuerpo, _), = (v for v in tpv.guardado.values() if v[0] == "admin/vincular")
    assert cuerpo["activo"] is False and cuerpo["tutor_ref"] == t.tutor_ref
    assert [a["id"] for a in tpv.tutores[t.tutor_ref]["alumnos"]] == [2]


def test_desvincular_alumno_que_no_es_suyo_404(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv, hijos=["Luis Pérez"], telefono="8340000001")
    tutor_tpv(db, op, tpv, hijos=["Ajeno"], telefono="8340000002")
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/desvincular")
    r = admin.post(f"/admin/tutores/{t.id}/desvincular", data={**campos, "alumno_id": 2})
    assert r.status_code == 404 and "admin/vincular" not in tpv.llamadas


def test_reenviar_cambia_contrasena_y_cierra_sesiones(db, op, admin, tpv, conduit, app):
    # alta real: tutor con acceso y sesión abierta
    alta = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    vieja = _contrasena(alta)
    t = db.query(Tutor).one()
    sesion = tutor_nuevo_cliente(app)
    assert entrar_tutor(sesion, "8341445807", vieja).status_code == 303
    assert db.query(Sesion).filter_by(tipo="tutor").count() == 1

    pagina = admin.get(f"/admin/tutores/{t.id}")
    campos = formulario(pagina, f"/admin/tutores/{t.id}/reenviar")
    r = admin.post(f"/admin/tutores/{t.id}/reenviar", data=campos)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    nueva = _contrasena(r)
    assert nueva != vieja
    db.expire_all()
    t = db.query(Tutor).one()
    assert verificar_contrasena(t.contrasena_hash, nueva) and t.debe_cambiar
    assert not verificar_contrasena(t.contrasena_hash, vieja)
    assert db.query(Sesion).filter_by(tipo="tutor").count() == 0
    assert entrar_tutor(tutor_nuevo_cliente(app), "8341445807", vieja).status_code != 303
    assert entrar_tutor(tutor_nuevo_cliente(app), "8341445807", nueva).status_code == 303
    # Conduit: llave distinta a la del alta
    assert [e["idempotency_key"] for e in conduit.enviados] == [f"acceso:{t.id}:1", f"acceso:{t.id}:2"]
    assert nueva in conduit.enviados[1]["text"]
    fila = db.query(Bitacora).filter_by(operacion="reenviar_acceso").one()
    assert fila.llave == f"acceso:{t.id}:2" and fila.http_status == 200


def test_segundo_reenvio_usa_el_intento_siguiente(db, op, admin, tpv, conduit):
    admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    t = db.query(Tutor).one()
    for esperado in (2, 3):
        campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/reenviar")
        assert admin.post(f"/admin/tutores/{t.id}/reenviar", data=campos).status_code == 200
        assert conduit.enviados[-1]["idempotency_key"] == f"acceso:{t.id}:{esperado}"
    assert db.query(Bitacora).filter_by(operacion="reenviar_acceso").count() == 2


def test_reenvio_repetido_con_el_formulario_viejo_no_genera_otra_contrasena(db, op, admin, tpv, conduit):
    admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    t = db.query(Tutor).one()
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/reenviar")
    assert admin.post(f"/admin/tutores/{t.id}/reenviar", data=campos).status_code == 200
    db.expire_all()
    h = db.query(Tutor).one().contrasena_hash
    r = admin.post(f"/admin/tutores/{t.id}/reenviar", data=campos)  # doble clic
    assert r.status_code == 409 and 'id="contrasena"' not in r.text
    db.expire_all()
    assert db.query(Tutor).one().contrasena_hash == h
    assert len(conduit.enviados) == 2


def test_reenviar_sin_alta_completa_no_genera_contrasena(db, op, admin, tpv, conduit):
    t = crear_tutor(db, op, tutor_ref="", contrasena="!")
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/reenviar")
    r = admin.post(f"/admin/tutores/{t.id}/reenviar", data=campos)
    assert r.status_code == 400 and 'id="contrasena"' not in r.text
    assert conduit.enviados == []
    assert db.query(Bitacora).filter_by(operacion="reenviar_acceso").count() == 0


def test_reenviar_no_existe_por_get(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv)
    assert admin.get(f"/admin/tutores/{t.id}/reenviar").status_code == 405


def test_reenviar_con_llave_de_cifrado_mala_no_toca_la_contrasena(db, op, admin, tpv, conduit):
    from cryptography.fernet import Fernet
    from portal.cifrado import cifrar
    t = tutor_tpv(db, op, tpv, contrasena="vieja-clave-1")
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/reenviar")
    op.conduit_secreto_cif = cifrar("x", Fernet.generate_key().decode())
    db.commit()
    r = admin.post(f"/admin/tutores/{t.id}/reenviar", data=campos)
    assert r.status_code == 500 and "configuración de WhatsApp" in r.text
    assert 'id="contrasena"' not in r.text
    db.expire_all()
    assert verificar_contrasena(db.query(Tutor).one().contrasena_hash, "vieja-clave-1")


def test_desactivar_corta_el_acceso(db, op, admin, tpv, conduit, app):
    alta = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    pw = _contrasena(alta)
    t = db.query(Tutor).one()
    sesion = tutor_nuevo_cliente(app)
    assert entrar_tutor(sesion, "8341445807", pw).status_code == 303
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/activo")
    llamadas = dict(tpv.llamadas)
    r = admin.post(f"/admin/tutores/{t.id}/activo", data={**campos, "activo": "0"})
    assert r.status_code == 200 and "Desactivado" in r.text
    db.expire_all()
    assert db.query(Tutor).one().activo is False
    assert db.query(Sesion).filter_by(tipo="tutor").count() == 0
    assert sesion.get("/", follow_redirects=False).status_code == 303
    assert entrar_tutor(tutor_nuevo_cliente(app), "8341445807", pw).status_code != 303
    assert tpv.llamadas.get("admin/vincular") is None
    assert {k: v for k, v in tpv.llamadas.items() if k != "admin/tutor/detalle"} == {
        k: v for k, v in llamadas.items() if k != "admin/tutor/detalle"}
    # y se puede volver a activar
    campos = formulario(r, f"/admin/tutores/{t.id}/activo")
    r = admin.post(f"/admin/tutores/{t.id}/activo", data={**campos, "activo": "1"})
    assert r.status_code == 200
    db.expire_all()
    assert db.query(Tutor).one().activo is True


def test_reenviar_a_tutor_desactivado_se_rechaza(db, op, admin, tpv, conduit):
    t = tutor_tpv(db, op, tpv, activo=False)
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/reenviar")
    r = admin.post(f"/admin/tutores/{t.id}/reenviar", data=campos)
    assert r.status_code == 400 and conduit.enviados == []


def test_los_post_de_la_ficha_exigen_csrf(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv, hijos=["Luis"])
    for ruta, extra in (("hijos", {"nombre": "x"}), ("vincular", {"otro_tutor_id": 1, "alumno_id": 1}),
                        ("desvincular", {"alumno_id": 1}), ("reenviar", {}), ("activo", {"activo": "0"})):
        r = admin.post(f"/admin/tutores/{t.id}/{ruta}",
                       data={"csrf": "mal", "llave": "11111111-aaaa", **extra})
        assert r.status_code == 403, ruta
    assert [k for k in tpv.llamadas if k != "admin/tutor/detalle"] == []
    db.expire_all()
    assert db.query(Tutor).one().activo is True


# ---- «Alta incompleta» y alta vieja ----

def test_alta_incompleta_es_sin_entrega_exitosa(db, op, admin, tpv, conduit):
    sin_alta = crear_tutor(db, op, telefono="8340000009", nombre="Sin entrega")
    r = admin.get("/admin")
    assert r.text.count("Alta incompleta") == 1
    # una fila reenviar_acceso con http_status cuenta, sea cual sea el intento
    db.add(Bitacora(operador_id=op.id, actor="x", operacion="reenviar_acceso",
                    llave=f"acceso:{sin_alta.id}:4", http_status=200))
    db.commit()
    assert admin.get("/admin").text.count("Alta incompleta") == 0
    # una fila interrumpida (sin http_status) no cuenta
    otro = crear_tutor(db, op, telefono="8340000010", nombre="Interrumpido")
    db.add(Bitacora(operador_id=op.id, actor="x", operacion="entregar_acceso",
                    llave=f"acceso:{otro.id}:1"))
    db.commit()
    assert admin.get("/admin").text.count("Alta incompleta") == 1
    # y la fila de otro tutor con id parecido (id seguido de «1») no lo marca como entregado
    db.add(Bitacora(operador_id=op.id, actor="x", operacion="entregar_acceso",
                    llave=f"acceso:{otro.id}1:1", http_status=200))
    db.commit()
    assert admin.get("/admin").text.count("Alta incompleta") == 1


def test_alta_incompleta_se_resuelve_con_un_reenvio(db, op, admin, tpv, conduit):
    t = tutor_tpv(db, op, tpv, nombre="Sin entrega")
    assert "Alta incompleta" in admin.get(f"/admin/tutores/{t.id}").text
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/reenviar")
    admin.post(f"/admin/tutores/{t.id}/reenviar", data=campos)
    assert "Alta incompleta" not in admin.get("/admin").text
    assert db.query(Bitacora).filter_by(operacion="reenviar_acceso").one().llave == f"acceso:{t.id}:1"


def test_formulario_de_alta_viejo_tras_un_reenvio_no_genera_contrasena(db, op, admin, tpv, conduit):
    datos = datos_alta(admin.get("/admin/alta"))
    tpv.fallar_en = {"admin/alumno/crear"}
    r = admin.post("/admin/alta", data=datos)  # alta a medias: hay tutor, no hay entrega
    tpv.fallar_en = set()
    assert r.status_code >= 400
    t = db.query(Tutor).one()
    # alguien reenvía el acceso desde la ficha antes de que se reintente el alta
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/reenviar")
    assert admin.post(f"/admin/tutores/{t.id}/reenviar", data=campos).status_code == 200
    db.expire_all()
    h = db.query(Tutor).one().contrasena_hash
    datos["csrf"] = csrf_de(admin.get("/admin"))
    r2 = admin.post("/admin/alta", data=datos)
    assert r2.status_code == 200 and "Este alta ya se completó" in r2.text
    assert 'id="contrasena"' not in r2.text
    db.expire_all()
    assert db.query(Tutor).one().contrasena_hash == h
    assert len(conduit.enviados) == 1


def test_ficha_con_otro_de_otro_operador_404(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv)
    ajeno = crear_tutor(db, crear_operador(db, host="otra.test"), telefono="8340000066")
    assert admin.get(f"/admin/tutores/{t.id}?otro={ajeno.id}").status_code == 404


def test_reenvio_con_envio_en_curso_se_rechaza(db, op, admin, tpv, conduit):
    admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta")))
    t = db.query(Tutor).one()
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/reenviar")
    # otro reenvío dejó su fila sin terminar hace un instante
    db.add(Bitacora(operador_id=op.id, actor="x", operacion="reenviar_acceso",
                    llave=f"acceso:{t.id}:2"))
    db.commit()
    campos["intento"] = "3"
    r = admin.post(f"/admin/tutores/{t.id}/reenviar", data=campos)
    assert r.status_code == 409 and "en curso" in r.text and 'id="contrasena"' not in r.text
    assert len(conduit.enviados) == 1


def test_ficha_muestra_estado_del_ultimo_envio(db, op, admin, tpv, conduit):
    t = tutor_tpv(db, op, tpv)
    assert 'id="ultima-entrega">ninguno<' in admin.get(f"/admin/tutores/{t.id}").text
    db.add(Bitacora(operador_id=op.id, actor="x", operacion="entregar_acceso", llave=f"acceso:{t.id}:1"))
    db.commit()
    assert "interrumpido" in admin.get(f"/admin/tutores/{t.id}").text
    db.add(Bitacora(operador_id=op.id, actor="x", operacion="reenviar_acceso",
                    llave=f"acceso:{t.id}:2", http_status=200, codigo_error="error"))
    db.commit()
    assert 'id="ultima-entrega">error<' in admin.get(f"/admin/tutores/{t.id}").text


def test_tutor_sin_referencia_no_ofrece_hijos_ni_se_ofrece_como_otro(db, op, admin, tpv):
    sin = crear_tutor(db, op, telefono="8340000050", tutor_ref="", nombre="Sin Ref")
    con = tutor_tpv(db, op, tpv, nombre="Con Ref", telefono="8340000051")
    r = admin.get(f"/admin/tutores/{sin.id}")
    assert r.status_code == 200 and "no tiene referencia" in r.text
    assert f"/admin/tutores/{sin.id}/hijos" not in r.text
    assert "Sin Ref (" not in admin.get(f"/admin/tutores/{con.id}").text
    campos = {"csrf": csrf_de(r), "llave": "11111111-aaaa", "nombre": "x", "alumno_id": 1,
              "otro_tutor_id": con.id}
    for ruta in ("hijos", "vincular", "desvincular"):
        rr = admin.post(f"/admin/tutores/{sin.id}/{ruta}", data=campos)
        assert rr.status_code == 400 and "no tiene referencia" in rr.text, ruta
    rr = admin.post(f"/admin/tutores/{con.id}/vincular", data={**campos, "otro_tutor_id": sin.id})
    assert rr.status_code == 404
    assert [k for k in tpv.llamadas if k != "admin/tutor/detalle"] == []


def test_llave_reutilizada_en_la_ficha_no_manda_a_la_ficha(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv)
    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/hijos")
    admin.post(f"/admin/tutores/{t.id}/hijos", data={**campos, "nombre": "Mateo", "matricula": ""})
    r = admin.post(f"/admin/tutores/{t.id}/hijos", data={**campos, "nombre": "Otro", "matricula": ""})
    assert r.status_code == 400 and "desde la ficha" not in r.text and "Recarga la ficha" in r.text


AVISO_BLOQUEO = "Recuperación bloqueada por intentos fallidos"


def _quemar(db, tutor, veces=2):
    from datetime import timedelta
    from sqlalchemy import func
    from portal.models import CodigoRecuperacion
    for _ in range(veces):
        db.add(CodigoRecuperacion(tutor_id=tutor.id, codigo_hash="x", intentos=5, usado=True,
                                  expira=func.now() + timedelta(minutes=5)))
    db.commit()


def _pedir_codigo(app, usuario="8341445807"):
    cl = tutor_nuevo_cliente(app)
    return cl.post("/recuperar", data={"usuario": usuario, "csrf": csrf_de(cl.get("/recuperar"))})


def test_tope_de_recuperacion_se_ve_en_la_ficha_y_el_reenvio_lo_libera(
        db, op, admin, tpv, conduit, app, caplog):
    import logging
    from portal.models import CodigoRecuperacion
    t = tutor_tpv(db, op, tpv)
    assert AVISO_BLOQUEO not in admin.get(f"/admin/tutores/{t.id}").text
    _quemar(db, t)
    assert AVISO_BLOQUEO in admin.get(f"/admin/tutores/{t.id}").text
    with caplog.at_level(logging.WARNING):
        assert _pedir_codigo(app).status_code == 303
    assert conduit.enviados == []
    avisos = [r.getMessage() for r in caplog.records if "bloqueada" in r.getMessage()]
    assert avisos == [f"Recuperación bloqueada por intentos fallidos: tutor={t.id}"]

    campos = formulario(admin.get(f"/admin/tutores/{t.id}"), f"/admin/tutores/{t.id}/reenviar")
    assert admin.post(f"/admin/tutores/{t.id}/reenviar", data=campos).status_code == 200
    assert AVISO_BLOQUEO not in admin.get(f"/admin/tutores/{t.id}").text
    n = len(conduit.enviados)
    assert _pedir_codigo(app).status_code == 303
    assert len(conduit.enviados) == n + 1
    assert db.query(CodigoRecuperacion).filter_by(usado=False).count() == 1


def test_el_tope_de_recuperacion_no_afecta_al_login(db, op, tpv, app):
    t = tutor_tpv(db, op, tpv, debe_cambiar=False)
    _quemar(db, t)
    assert entrar_tutor(tutor_nuevo_cliente(app), "8341445807", "temporal123").headers["location"] == "/"
