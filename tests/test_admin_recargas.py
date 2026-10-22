from decimal import Decimal

from portal.models import Recarga
from tests.fabricas import crear_operador, crear_tutor
from tests.test_admin_alta import admin, c, entrar_admin, op  # noqa: F401  fixtures compartidas


def _recarga(db, op, tutor, llave, estado, error=None, total=100):
    r = Recarga(operador_id=op.id, tutor_id=tutor.id, llave=llave, total=Decimal(total),
                reparto=[{"alumno_id": 1, "nombre": "Luis", "importe": total}], estado=estado,
                error=error, mp_pago_id=str(770 + ord(llave[0]) - 97) if estado != "pendiente" else None)
    db.add(r)
    db.commit()
    return r


def test_lista_con_revision_arriba(db, op, admin):
    t = crear_tutor(db, op, debe_cambiar=False)
    _recarga(db, op, t, "a" * 10, "acreditada", total=150)
    _recarga(db, op, t, "b" * 10, "revision", error="El TPV rechazó la recarga (pago_ya_acreditado)")
    r = admin.get("/admin/recargas")
    assert r.status_code == 200
    assert r.text.index("pago_ya_acreditado") < r.text.index("$150.00")
    assert "Ana López" in r.text and "771" in r.text


def test_filtro_por_estado(db, op, admin):
    t = crear_tutor(db, op, debe_cambiar=False)
    _recarga(db, op, t, "a" * 10, "acreditada", total=150)
    _recarga(db, op, t, "b" * 10, "vencida", total=60)
    r = admin.get("/admin/recargas?estado=vencida")
    assert "$60.00" in r.text and "$150.00" not in r.text


def test_no_ve_las_de_otro_operador(db, op, admin):
    otro = crear_operador(db, host="otra.test")
    t = crear_tutor(db, otro, telefono="8340000001", debe_cambiar=False)
    _recarga(db, otro, t, "z" * 10, "revision", error="ajena")
    assert "ajena" not in admin.get("/admin/recargas").text


def test_enlace_desde_la_lista_de_tutores(admin):
    assert 'href="/admin/recargas"' in admin.get("/admin").text


def test_sin_sesion(op, c):
    assert c.get("/admin/recargas").status_code == 303


def test_pago_extra_se_ve_arriba(db, op, admin):
    t = crear_tutor(db, op, debe_cambiar=False)
    r = _recarga(db, op, t, "c" * 10, "acreditada", total=80)
    r.pagos_extra = ["555"]
    _recarga(db, op, t, "d" * 10, "acreditada", total=150)
    db.commit()
    html = admin.get("/admin/recargas").text
    assert "Pago extra" in html and "555" in html
    assert html.index("555") < html.index("$150.00")
