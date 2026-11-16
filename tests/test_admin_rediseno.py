"""Rediseño del portal del administrador: anticipación 1–30, logo, nombre por partes, alta con
hijos uno por uno."""
import io
import re
import struct
import uuid
import zlib

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from portal.models import Bitacora, ConfigPedidos, Operador, Tutor
from tests.fabricas import csrf_de, texto
from tests.test_admin_alta import (  # noqa: F401  fixtures compartidas
    HIJOS, admin, c, conduit, datos_alta, op, tpv,
)
from tests.test_admin_tutor import tutor_tpv



def _png(ancho=1, alto=1) -> bytes:
    """PNG real y mínimo (escala de grises, 1 bit)."""
    def bloque(tipo, datos):
        return (struct.pack(">I", len(datos)) + tipo + datos
                + struct.pack(">I", zlib.crc32(tipo + datos) & 0xffffffff))
    filas = b"".join(b"\0" + b"\0" * ((ancho + 7) // 8) for _ in range(alto))
    return (b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 1, 0, 0, 0, 0))
            + bloque(b"IDAT", zlib.compress(filas)) + bloque(b"IEND", b""))


PNG = _png()


def _con_acceso(db, op, tutor):
    """Padre de familia con su acceso ya entregado (alta terminada)."""
    db.add(Bitacora(operador_id=op.id, actor="x", operacion="entregar_acceso",
                    llave=f"acceso:{tutor.id}:1", http_status=200))
    db.commit()
    return tutor


def test_anticipacion_de_1_a_30(db, op, admin):
    p = admin.get("/admin/pedidos")
    assert 'max="30"' in p.text and "(1 a 30)" in p.text
    base = {"csrf": csrf_de(p), "hora_limite": "", "dias": ["0", "1"],
            "zona_horaria": "America/Mexico_City"}
    r = admin.post("/admin/pedidos", data={**base, "dias_anticipacion": "31"})
    assert r.status_code == 400 and "del 1 al 30" in r.text
    r = admin.post("/admin/pedidos", data={**base, "dias_anticipacion": "30"})
    assert r.status_code == 200
    assert db.scalars(select(ConfigPedidos)).one().dias_anticipacion == 30


def test_subir_y_quitar_logo(db, op, admin, c):
    p = admin.get("/admin/cafeteria")
    r = admin.post("/admin/cafeteria/logo", data={"csrf": csrf_de(p)},
                   files={"logo": ("logo.png", PNG, "image/png")})
    assert r.status_code == 200 and "Logo guardado" in r.text
    db.expire_all()
    o = db.get(Operador, op.id)
    assert o.logo == PNG and o.logo_tipo == "image/png" and len(o.logo_version) == 12
    assert c.get(f"/logo?v={o.logo_version}").content == PNG
    r = admin.post("/admin/cafeteria/logo/quitar", data={"csrf": csrf_de(r)})
    db.expire_all()
    assert "Logo quitado" in r.text and db.get(Operador, op.id).logo is None


def test_logo_rechaza_svg_y_archivos_grandes(db, op, admin):
    p = admin.get("/admin/cafeteria")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    for nombre, datos, tipo in [("x.svg", svg, "image/png"),          # el tipo del navegador no se cree
                                ("x.png", PNG + b"\0" * (512 * 1024), "image/png"),
                                ("x.png", b"", "image/png"),
                                ("x.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 64, "image/png"),  # sólo la firma
                                ("x.png", _png(5000, 1), "image/png"),                      # demasiado ancha
                                ("x.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 40, "image/jpeg"),
                                ("x.jpg", bytes.fromhex("ffd8ffc0ffd9"), "image/jpeg")]:
        r = admin.post("/admin/cafeteria/logo", data={"csrf": csrf_de(p)},
                       files={"logo": (nombre, datos, tipo)})
        assert r.status_code == 400 and "PNG, JPG o WebP" in r.text, nombre
    db.expire_all()
    assert db.get(Operador, op.id).logo is None


def _con_pillow(formato: str, **opciones) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (201, 138, 79)).save(buf, formato, **opciones)
    return buf.getvalue()


def _webp_grande() -> bytes:
    """WebP de 5000×1: rebasa el lado máximo (se rechaza antes de que Pillow reserve el lienzo)."""
    buf = io.BytesIO()
    Image.effect_noise((5000, 1), 60).convert("RGB").save(buf, "WEBP", lossless=True)
    return buf.getvalue()


def test_webp_enorme_se_rechaza_sin_abrirlo(monkeypatch):
    from portal.rutas import admin as rutas_admin
    datos = _webp_grande()
    assert len(datos) >= rutas_admin.LOGO_MIN

    def no_abrir(*a, **k):
        raise AssertionError("Pillow no debe abrir un WebP que rebasa el lado máximo")
    monkeypatch.setattr(rutas_admin.Image, "open", no_abrir)
    assert rutas_admin._tipo_logo(datos) is None


@pytest.mark.parametrize("formato,tipo", [("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_logo_acepta_jpeg_y_webp(db, op, admin, formato, tipo):
    datos = _con_pillow(formato)
    p = admin.get("/admin/cafeteria")
    r = admin.post("/admin/cafeteria/logo", data={"csrf": csrf_de(p)},
                   files={"logo": ("logo.bin", datos, "application/octet-stream")})
    assert r.status_code == 200 and "Logo guardado" in r.text
    db.expire_all()
    assert db.get(Operador, op.id).logo_tipo == tipo


def test_logo_rechaza_imagenes_que_no_se_decodifican(db, op, admin):
    """Firma y estructura correctas no bastan: la imagen tiene que decodificarse completa."""
    png = _png(64, 64)
    i = png.index(b"IDAT") + 4
    corrupto = png[:i] + bytes(b ^ 0xff for b in png[i:i + 8]) + png[i + 8:]
    jpeg = _con_pillow("JPEG")
    buf = io.BytesIO()
    cuadros = [Image.new("RGB", (8, 8), c) for c in ("red", "blue")]
    cuadros[0].save(buf, "WEBP", save_all=True, append_images=cuadros[1:])
    p = admin.get("/admin/cafeteria")
    grande = _png(600, 600)  # cortado sigue arriba de LOGO_MIN: lo rechaza la validación, no el tamaño
    assert len(grande) - 12 >= 64
    i = png.index(b"IDAT")
    largo = struct.unpack(">I", png[i - 4:i])[0]
    mala_suma = png[:i + 4 + largo] + b"\0\0\0\0" + png[i + 8 + largo:]   # pixeles bien, CRC mal
    for nombre, datos in [("corrupto.png", corrupto),
                          ("sin-iend.png", grande[:-12]),
                          ("iend-cortado.png", grande[:-4]),
                          ("iend-mala-suma.png", grande[:-4] + b"\0" * 4),
                          ("enorme.webp", _webp_grande()),
                          ("mala-suma.png", mala_suma),
                          ("cortado.jpg", jpeg[:len(jpeg) // 2]),
                          ("animado.webp", buf.getvalue()),
                          ("x.gif", _con_pillow("GIF"))]:
        r = admin.post("/admin/cafeteria/logo", data={"csrf": csrf_de(p)},
                       files={"logo": (nombre, datos, "image/png")})
        assert r.status_code == 400 and "PNG, JPG o WebP" in r.text, nombre
    db.expire_all()
    assert db.get(Operador, op.id).logo is None


def test_logo_exige_csrf(db, op, admin):
    r = admin.post("/admin/cafeteria/logo", data={"csrf": "x"},
                   files={"logo": ("logo.png", PNG, "image/png")})
    assert r.status_code == 403


def test_alta_con_nombre_por_partes(db, op, admin, tpv, conduit):
    p = admin.get("/admin/alta")
    d = datos_alta(p, nombre="", nombres="María José", apellido_1="Pérez", apellido_2="Ruiz")
    r = admin.post("/admin/alta", data=d)
    assert r.status_code == 200, r.text
    t = db.scalars(select(Tutor)).one()
    assert (t.nombre, t.nombres, t.apellido_1, t.apellido_2) == (
        "María José Pérez Ruiz", "María José", "Pérez", "Ruiz")
    assert tpv.tutores[t.tutor_ref]["nombre"] == "María José Pérez Ruiz"


def test_alta_sin_apellido_1_se_rechaza(db, op, admin, tpv):
    p = admin.get("/admin/alta")
    r = admin.post("/admin/alta", data=datos_alta(p, nombre="", nombres="María", apellido_1=""))
    assert r.status_code == 400 and "apellido 1" in r.text and tpv.llamadas == {}


def test_alta_con_mas_de_10_hijos_se_rechaza(db, op, admin, tpv):
    p = admin.get("/admin/alta")
    once = {"hijo_nombres": [f"Hijo{i}" for i in range(11)], "hijo_paterno": ["Pérez"] * 11,
            "hijo_materno": [""] * 11, "hijo_matricula": [""] * 11}
    r = admin.post("/admin/alta", data={**datos_alta(p), **once})
    assert r.status_code == 400 and "Máximo 10 hijos" in r.text and tpv.llamadas == {}


def test_alta_acepta_mas_de_5_hijos(db, op, admin, tpv, conduit):
    p = admin.get("/admin/alta")
    seis = {"hijo_nombres": [f"Hijo{i}" for i in range(6)], "hijo_paterno": ["Pérez"] * 6,
            "hijo_materno": [""] * 6, "hijo_matricula": [""] * 6}
    r = admin.post("/admin/alta", data={**datos_alta(p), **seis})
    assert r.status_code == 200 and tpv.llamadas.get("admin/alumno/crear") == 6


def test_alta_de_antes_con_nombre_de_una_palabra(db, op, admin, tpv, conduit):
    p = admin.get("/admin/alta")
    r = admin.post("/admin/alta", data=datos_alta(p, nombre="Cher"))
    assert r.status_code == 200
    assert db.scalars(select(Tutor)).one().nombre == "Cher"


def test_alta_no_descarta_hijos_en_silencio(db, op, admin, tpv):
    p = admin.get("/admin/alta")
    nombres = [f"Hijo{i}" for i in range(10)] + ["", "Extra"]
    filas = {"hijo_nombres": nombres, "hijo_paterno": ["Pérez" if n else "" for n in nombres],
             "hijo_materno": [""] * 12, "hijo_matricula": [""] * 12}
    r = admin.post("/admin/alta", data={**datos_alta(p), **filas})
    assert r.status_code == 400 and "Máximo 10 hijos" in r.text and tpv.llamadas == {}


def test_nombre_completo_de_antes_se_reenvia_tal_cual(db, op, admin, tpv):
    """Si el TPV creó al padre de familia pero se perdió la respuesta, el reintento debe mandar
    exactamente lo mismo (la llave lo exige)."""
    tpv.fallar_en = {"admin/tutor/crear"}
    r = admin.post("/admin/alta", data=datos_alta(admin.get("/admin/alta"), nombre="Ana  María Pérez"))
    assert '<input type="hidden" name="nombre" value="Ana  María Pérez">' in r.text
    assert 'name="nombres"' not in r.text  # las partes sólo se muestran


def test_hijos_de_regreso_con_error_son_editables(db, op, admin, tpv):
    p = admin.get("/admin/alta")
    r = admin.post("/admin/alta", data=datos_alta(p, hijo_nombres=["Luis"], hijo_paterno=[""],
                                                  hijo_materno=[""], hijo_matricula=[""]))
    assert r.status_code == 400
    assert '<input name="hijo_nombres" value="Luis"' in r.text  # se corrige ahí mismo, sin JS


def test_no_se_corrige_el_nombre_con_el_alta_a_medias(db, op, admin, tpv):
    t = tutor_tpv(db, op, tpv, nombre="Ana Perez")  # sin entrega de acceso: alta incompleta
    f = admin.get(f"/admin/tutores/{t.id}")
    assert "Corregir nombre" not in f.text
    r = admin.post(f"/admin/tutores/{t.id}/nombre", data={
        "csrf": csrf_de(f), "llave": str(uuid.uuid4()), "nombres": "Bea", "apellido_1": "Pérez"})
    assert r.status_code == 400 and "Termina el alta" in r.text
    assert "admin/tutor/editar" not in tpv.llamadas


# ---------------------------------------------------------------- corregir el nombre por partes

def _form_nombre(admin, t) -> dict:
    """Campos ocultos del formulario «Corregir nombre» de la ficha (csrf, llave y huella)."""
    f = admin.get(f"/admin/tutores/{t.id}").text
    form = f.split('action="/admin/tutores/%d/nombre"' % t.id)[1].split("</form>")[0]
    campo = lambda n: re.search(f'name="{n}" value="([^"]*)"', form).group(1)
    return {"csrf": campo("csrf"), "llave": campo("llave"), "antes": campo("antes")}


def test_corregir_nombre_por_partes(db, op, admin, tpv):
    t = _con_acceso(db, op, tutor_tpv(db, op, tpv, nombre="Ana Karen Lopez"))
    r = admin.post(f"/admin/tutores/{t.id}/nombre", data={
        **_form_nombre(admin, t), "nombres": "Ana Karen", "apellido_1": "López", "apellido_2": "Soto"})
    assert r.status_code == 200 and "Nombre corregido" in r.text
    db.expire_all()
    t = db.get(Tutor, t.id)
    assert (t.nombre, t.nombres, t.apellido_1, t.apellido_2) == (
        "Ana Karen López Soto", "Ana Karen", "López", "Soto")
    assert tpv.tutores[t.tutor_ref]["nombre"] == "Ana Karen López Soto"
    assert "Ana Karen López Soto" in texto(r.text)


def test_corregir_nombre_valida_partes_y_largo(db, op, admin, tpv):
    t = _con_acceso(db, op, tutor_tpv(db, op, tpv))
    for datos in ({"nombres": "Ana", "apellido_1": ""}, {"nombres": "A" * 100, "apellido_1": "B" * 100}):
        r = admin.post(f"/admin/tutores/{t.id}/nombre", data={**_form_nombre(admin, t), **datos})
        assert r.status_code == 400 and "apellido 1" in r.text
    assert "admin/tutor/editar" not in tpv.llamadas


def test_pagina_vieja_no_deshace_una_correccion_posterior(db, op, admin, tpv):
    t = _con_acceso(db, op, tutor_tpv(db, op, tpv, nombre="Ana Perez"))
    vieja = {**_form_nombre(admin, t), "nombres": "Ana", "apellido_1": "María", "apellido_2": "Pérez"}
    admin.post(f"/admin/tutores/{t.id}/nombre", data=vieja)
    admin.post(f"/admin/tutores/{t.id}/nombre", data={
        **_form_nombre(admin, t), "nombres": "Ana María", "apellido_1": "Pérez"})
    r = admin.post(f"/admin/tutores/{t.id}/nombre", data=vieja)  # se reenvía la primera
    assert r.status_code == 409 and "cambió desde que abriste la ficha" in r.text
    db.expire_all()
    t = db.get(Tutor, t.id)
    assert (t.nombres, t.apellido_1, t.apellido_2) == ("Ana María", "Pérez", None)
    assert tpv.tutores[t.tutor_ref]["nombre"] == "Ana María Pérez"


def test_confirmacion_fallida_no_toca_la_ficha_y_el_reintento_la_aplica(db, op, admin, tpv):
    t = _con_acceso(db, op, tutor_tpv(db, op, tpv, nombre="Ana Perez"))
    caido = {"si": True}

    def detalle(req):
        if (caido["si"] and req.url.path.endswith("admin/tutor/detalle")
                and "admin/tutor/editar" in tpv.llamadas):
            return httpx.Response(503, json={"ok": False, "error": {"codigo": "x", "mensaje": "x"}})
        return tpv._manejar(req)

    admin.app.state.tpv_transport = httpx.MockTransport(detalle)
    datos = {**_form_nombre(admin, t), "nombres": "Ana María", "apellido_1": "Pérez"}
    caido["si"] = True
    r = admin.post(f"/admin/tutores/{t.id}/nombre", data=datos)
    assert "no pudimos confirmar" in r.text
    db.expire_all()
    assert db.get(Tutor, t.id).nombre == "Ana Perez"  # la ficha no se tocó
    caido["si"] = False
    admin.post(f"/admin/tutores/{t.id}/nombre", data=datos)  # mismo formulario, misma llave
    db.expire_all()
    t = db.get(Tutor, t.id)
    assert (t.nombre, t.nombres, t.apellido_1) == ("Ana María Pérez", "Ana María", "Pérez")


def test_cambiar_solo_las_partes(db, op, admin, tpv):
    t = _con_acceso(db, op, tutor_tpv(db, op, tpv, nombre="Ana María Pérez"))
    r = admin.post(f"/admin/tutores/{t.id}/nombre", data={
        **_form_nombre(admin, t), "nombres": "Ana María", "apellido_1": "Pérez"})
    assert "Nombre corregido" in r.text
    db.expire_all()
    t = db.get(Tutor, t.id)
    assert (t.nombre, t.nombres, t.apellido_1, t.apellido_2) == (
        "Ana María Pérez", "Ana María", "Pérez", None)
    assert tpv.tutores[t.tutor_ref]["nombre"] == "Ana María Pérez"
