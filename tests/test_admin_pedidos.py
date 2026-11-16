from datetime import date, time

from sqlalchemy import select

from portal.models import ConfigPedidos, DiaSinServicio
from portal.reglas import cargar_reglas
from tests.fabricas import crear_admin, crear_operador, csrf_de
from tests.test_admin_alta import admin, c, entrar_admin, op  # noqa: F401  fixtures compartidas


def datos(pagina, **kw):
    d = {"csrf": csrf_de(pagina), "hora_limite": "09:30", "dias": ["0", "1", "2", "3"],
         "zona_horaria": "America/Monterrey", "dias_anticipacion": "7"}
    d.update(kw)
    return d


def test_guardar_config(db, op, admin):
    p = admin.get("/admin/pedidos")
    assert p.status_code == 200
    r = admin.post("/admin/pedidos", data=datos(p))
    assert r.status_code == 200 and "Guardado" in r.text
    db.expire_all()
    cfg = db.scalars(select(ConfigPedidos)).one()
    assert (cfg.operador_id, cfg.hora_limite, cfg.dias_servicio) == (op.id, time(9, 30), "0123")
    assert (cfg.zona_horaria, cfg.dias_anticipacion) == ("America/Monterrey", 7)
    # segundo guardado actualiza la misma fila
    admin.post("/admin/pedidos", data=datos(p, hora_limite="10:00"))
    db.expire_all()
    assert db.scalars(select(ConfigPedidos)).one().hora_limite == time(10, 0)
    assert cargar_reglas(db, op.id).anticipacion == 7


def test_hora_vacia_es_sin_limite(db, op, admin):
    p = admin.get("/admin/pedidos")
    admin.post("/admin/pedidos", data=datos(p, hora_limite=""))
    db.expire_all()
    assert db.scalars(select(ConfigPedidos)).one().hora_limite is None


def test_validaciones(db, op, admin):
    p = admin.get("/admin/pedidos")
    for campo, malo in [("hora_limite", "25:00"), ("hora_limite", "9am"), ("dias", []),
                        ("dias", ["7"]), ("zona_horaria", "Europe/Madrid"),
                        ("dias_anticipacion", "0"), ("dias_anticipacion", "31"),
                        ("dias_anticipacion", "abc")]:
        r = admin.post("/admin/pedidos", data=datos(p, **{campo: malo}))
        assert r.status_code == 400, (campo, malo)
        assert 'role="alert"' in r.text
    assert db.scalars(select(ConfigPedidos)).first() is None


def test_agregar_y_borrar_dia_sin_servicio(db, op, admin):
    p = admin.get("/admin/pedidos")
    r = admin.post("/admin/pedidos/sin-servicio",
                   data={"csrf": csrf_de(p), "fecha": "2026-12-25", "motivo": "Navidad"})
    assert r.status_code == 200 and "Navidad" in r.text
    d = db.scalars(select(DiaSinServicio)).one()
    assert d.fecha == date(2026, 12, 25) and d.operador_id == op.id
    # repetir la misma fecha no duplica ni truena
    r = admin.post("/admin/pedidos/sin-servicio", data={"csrf": csrf_de(p), "fecha": "2026-12-25"})
    assert r.status_code == 400
    r = admin.post("/admin/pedidos/sin-servicio", data={"csrf": csrf_de(p), "fecha": "no-es-fecha"})
    assert r.status_code == 400
    assert date(2026, 12, 25) in cargar_reglas(db, op.id).sin_servicio
    r = admin.post(f"/admin/pedidos/sin-servicio/{d.id}/borrar", data={"csrf": csrf_de(p)})
    assert r.status_code == 200
    db.expire_all()
    assert db.scalars(select(DiaSinServicio)).first() is None


def test_aviso_sin_configurar_en_panel(db, op, admin):
    r = admin.get("/admin")
    assert "Horario de pedidos sin configurar" in r.text and 'href="/admin/pedidos"' in r.text
    p = admin.get("/admin/pedidos")
    admin.post("/admin/pedidos", data=datos(p))
    assert "Horario de pedidos sin configurar" not in admin.get("/admin").text


def test_otro_operador_no_ve_ni_borra(db, op, admin):
    otro = crear_operador(db, host="otra.test")
    d = DiaSinServicio(operador_id=otro.id, fecha=date(2026, 12, 25), motivo="Ajeno")
    db.add(d)
    db.commit()
    p = admin.get("/admin/pedidos")
    assert "Ajeno" not in p.text
    r = admin.post(f"/admin/pedidos/sin-servicio/{d.id}/borrar", data={"csrf": csrf_de(p)})
    assert r.status_code == 404
    db.expire_all()
    assert db.scalars(select(DiaSinServicio)).one().motivo == "Ajeno"


def test_sin_csrf_403(db, op, admin):
    assert admin.post("/admin/pedidos", data={"hora_limite": "09:00", "dias": ["0"],
                                              "zona_horaria": "America/Mexico_City",
                                              "dias_anticipacion": "7"}).status_code == 403
    assert admin.post("/admin/pedidos/sin-servicio", data={"fecha": "2026-12-25"}).status_code == 403
    assert admin.post("/admin/pedidos/sin-servicio/1/borrar", data={}).status_code == 403
    assert db.scalars(select(ConfigPedidos)).first() is None


def test_tutor_no_entra(db, op, c):
    from tests.fabricas import crear_tutor, entrar_tutor
    crear_tutor(db, op, debe_cambiar=False)
    entrar_tutor(c, "8341445807", "temporal123")
    for ruta in ("/admin/pedidos",):
        assert c.get(ruta).status_code in (303, 401, 403)
    assert c.post("/admin/pedidos", data={}).status_code in (303, 401, 403)
    assert db.scalars(select(ConfigPedidos)).first() is None


def test_dias_sin_servicio_valen_sin_config(db, op):
    db.add(DiaSinServicio(operador_id=op.id, fecha=date(2026, 12, 25)))
    db.commit()
    r = cargar_reglas(db, op.id)
    assert r.anticipacion == 14 and r.sin_servicio == frozenset({date(2026, 12, 25)})
