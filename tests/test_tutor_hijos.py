import pytest
from fastapi.testclient import TestClient

from portal.tutor_tpv import MSG_SIN_TPV
from tests.fabricas import crear_operador, crear_tutor, entrar_tutor
from tests.tpv_falso import TPVFalso


@pytest.fixture
def op(db):
    return crear_operador(db)


@pytest.fixture
def tpv(app):
    f = TPVFalso(secreto_tutor="secreto-tutor")
    app.state.tpv_transport = f.transport
    return f


@pytest.fixture
def c(app):
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


@pytest.fixture
def tutor(db, op):
    return crear_tutor(db, op, debe_cambiar=False)


def _entrar(c):
    assert entrar_tutor(c, "8341445807", "temporal123").status_code == 303


def test_inicio_muestra_tarjetas_con_saldo(tutor, tpv, c):
    tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", disponible=150, retenido=30)
    tpv.agregar_hijo(tutor.tutor_ref, "Sofía Pérez", disponible=1234.5)
    _entrar(c)
    r = c.get("/")
    assert r.status_code == 200
    assert "Luis Pérez" in r.text and "Sofía Pérez" in r.text
    assert "$150.00" in r.text and "$30.00" in r.text and "$1,234.50" in r.text


def test_un_solo_hijo_redirige_a_su_pantalla(tutor, tpv, c):
    h = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez")
    _entrar(c)
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"] == f"/hijos/{h['id']}"


def test_sin_hijos_lo_dice(tutor, tpv, c):
    tpv.agregar_tutor(tutor.tutor_ref)
    _entrar(c)
    r = c.get("/")
    assert r.status_code == 200 and "Aún no tienes hijos" in r.text


def test_tpv_caido_muestra_mensaje_sin_cifras(tutor, tpv, c):
    tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", disponible=150)
    tpv.agregar_hijo(tutor.tutor_ref, "Sofía Pérez", disponible=20)
    _entrar(c)
    tpv.fallar_en = {"tutor/alumnos"}
    r = c.get("/")
    assert r.status_code == 200 and MSG_SIN_TPV in r.text and "$" not in r.text
    r = c.get("/hijos/1")
    assert r.status_code == 200 and MSG_SIN_TPV in r.text and "$" not in r.text


def test_movimientos_caidos_no_tumban_la_ficha(tutor, tpv, c):
    h = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", disponible=150)
    _entrar(c)
    tpv.fallar_en = {"alumno/movimientos"}
    r = c.get(f"/hijos/{h['id']}")
    assert r.status_code == 200 and "$150.00" in r.text and MSG_SIN_TPV in r.text
    r = c.get(f"/hijos/{h['id']}/movimientos?pagina=2")
    assert r.status_code == 200 and MSG_SIN_TPV in r.text


def test_hijo_ajeno_404(db, op, tutor, tpv, c):
    ajeno = crear_tutor(db, op, telefono="8340000001")
    mio = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez")
    otro = tpv.agregar_hijo(ajeno.tutor_ref, "Ajeno")
    _entrar(c)
    assert c.get(f"/hijos/{mio['id']}").status_code == 200
    assert c.get(f"/hijos/{otro['id']}").status_code == 404
    assert c.get(f"/hijos/{otro['id']}/movimientos").status_code == 404
    assert c.get("/hijos/9999").status_code == 404
    # el movimiento ajeno nunca se pidió
    antes = tpv.llamadas.get("alumno/movimientos", 0)
    c.get(f"/hijos/{otro['id']}/movimientos")
    assert tpv.llamadas.get("alumno/movimientos", 0) == antes


def test_hijo_muestra_movimientos_con_signo(tutor, tpv, c):
    h = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", disponible=55)
    tpv.movimientos[h["id"]] = [
        {"fecha": "2026-10-21 15:00:00", "tipo": "compra", "descripcion": "Compra",
         "disponible": -45, "retenido": 0, "venta": "P1"},
        {"fecha": "2026-10-20 14:00:00", "tipo": "recarga", "descripcion": "Recarga",
         "disponible": 100, "retenido": 0, "venta": ""},
    ]
    _entrar(c)
    t = c.get(f"/hijos/{h['id']}").text
    assert "+$100.00" in t and "-$45.00" in t
    assert t.index("Compra") < t.index("Recarga")
    assert "mié 21/10/2026" in t
    assert "Ver más" not in t


def test_movimientos_paginados(tutor, tpv, c):
    h = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez")
    tpv.movimientos[h["id"]] = [
        {"fecha": "2026-10-21 10:00:00", "tipo": "recarga", "descripcion": f"Mov{i:02d}",
         "disponible": 1, "retenido": 0, "venta": ""} for i in range(45)]
    _entrar(c)
    p1 = c.get(f"/hijos/{h['id']}").text
    assert p1.count('class="movimiento"') == 20 and "Ver más" in p1
    p2 = c.get(f"/hijos/{h['id']}/movimientos?pagina=2").text
    assert p2.count('class="movimiento"') == 20 and "Ver más" in p2
    p3 = c.get(f"/hijos/{h['id']}/movimientos?pagina=3").text
    assert p3.count('class="movimiento"') == 5 and "Ver más" not in p3
    assert c.get(f"/hijos/{h['id']}/movimientos?pagina=0").status_code == 422


def test_credencial_bloqueada_aparece_en_tarjeta(tutor, tpv, c):
    tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", credenciales=[
        {"id": 1, "tipo": "nfc", "bloqueada": True}, {"id": 2, "tipo": "qr", "bloqueada": False}])
    tpv.agregar_hijo(tutor.tutor_ref, "Sofía Pérez")
    _entrar(c)
    t = c.get("/").text
    assert "Bloqueada: Tarjeta" in t and "Código QR" not in t


def test_formato():
    from datetime import date
    from decimal import Decimal
    from portal.formato import cambio, dinero, fecha
    assert dinero(1234.5) == "$1,234.50" and dinero(Decimal("0.005")) == "$0.01"
    assert dinero(-3) == "-$3.00" and cambio(0) == ""
    assert fecha(date(2026, 10, 22)) == "jue 22/10/2026"
    assert fecha("2026-10-21 15:00:00") == "mié 21/10/2026"
    assert fecha("2026-10-22 01:00:00") == "mié 21/10/2026"  # UTC -> hora local


def test_escribir_tutor_usa_el_tutor_ref_de_la_sesion(db, op, tutor, tpv, app):
    from fastapi import Request
    from portal.models import Bitacora
    from portal.tutor_tpv import escribir_tutor

    visto = {}
    original = tpv._operar

    def espia(opn, d):
        visto.update(d)
        return original(opn, d)

    tpv._operar = espia
    scope = {"type": "http", "app": app, "headers": [], "method": "POST", "path": "/"}
    # La operación no existe en el falso (404): lo que importa es lo que viajó y la bitácora.
    from portal.tpv import ErrorTPV
    with pytest.raises(ErrorTPV):
        escribir_tutor(db, Request(scope), op, tutor, "alumno/permiso",
                       {"llave": "k", "tutor_ref": "EVIL"})
    db.commit()
    assert visto["tutor_ref"] == tutor.tutor_ref != "EVIL"
    fila = db.query(Bitacora).one()
    assert fila.actor == f"tutor:{tutor.id}" and fila.llave == "k"


def test_valores_raros_del_tpv_no_dan_500(tutor, tpv, c):
    from portal.formato import cambio, dinero
    for malo in ("abc", None, "NaN", "Infinity"):
        assert dinero(malo) == "—" == cambio(malo)
    h = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", disponible="abc", retenido=None)
    tpv.movimientos[h["id"]] = [{"fecha": "basura", "tipo": "x", "descripcion": "Raro",
                                 "disponible": "NaN", "retenido": None, "venta": ""}]
    _entrar(c)
    r = c.get(f"/hijos/{h['id']}")
    assert r.status_code == 200 and "Raro" in r.text


def test_ver_mas_fallido_ofrece_reintentar(tutor, tpv, c):
    h = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez")
    _entrar(c)
    tpv.fallar_en = {"alumno/movimientos"}
    t = c.get(f"/hijos/{h['id']}/movimientos?pagina=2").text
    assert MSG_SIN_TPV in t and "Reintentar" in t and "pagina=2" in t
