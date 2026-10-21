from datetime import datetime, timedelta, timezone

import pytest
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient

from portal.models import Admin, Operador, Sesion, Tutor
from portal.seguridad import hash_contrasena
from portal.sesiones import (
    DURACION, crear_sesion, cerrar_sesiones, poner_cookie, borrar_cookie,
    sesion_admin, sesion_tutor, verificar_csrf,
)
from fastapi import Response


def _operador(db, host="cafeteria.test"):
    op = Operador(nombre="A", host=host, tpv_url="https://t", tpv_secreto_cif="x",
                  tpv_secreto_admin_cif="y")
    db.add(op)
    db.commit()
    return op


def _tutor(db, op):
    t = Tutor(operador_id=op.id, tutor_ref="T1", nombre="Ana", telefono="8341445807",
              contrasena_hash=hash_contrasena("x" * 8))
    db.add(t)
    db.commit()
    return t


def _admin(db, op):
    a = Admin(operador_id=op.id, usuario="adm", nombre="Adm",
              contrasena_hash=hash_contrasena("x" * 8))
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def cliente(app):
    router = APIRouter()

    @router.get("/_t")
    def t(par=Depends(sesion_tutor)):
        return {"tutor": par[1].id}

    @router.get("/_a")
    def a(par=Depends(sesion_admin)):
        return {"admin": par[1].id}

    @router.post("/_p")
    async def p(request: Request, par=Depends(sesion_tutor)):
        await verificar_csrf(request, par[0])
        return {"ok": True}

    app.include_router(router)
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


def test_duraciones():
    assert DURACION["tutor"] == timedelta(days=30)
    assert DURACION["admin"] == timedelta(hours=12)


def test_token_se_guarda_hasheado(db):
    op = _operador(db)
    tok = crear_sesion(db, "tutor", _tutor(db, op).id, op.id)
    s = db.query(Sesion).one()
    assert s.token_hash != tok and len(s.token_hash) == 64 and len(s.csrf) >= 32


def test_sesion_valida(db, cliente):
    op = _operador(db)
    t = _tutor(db, op)
    cliente.cookies.set("sb_sesion", crear_sesion(db, "tutor", t.id, op.id))
    r = cliente.get("/_t")
    assert r.status_code == 200 and r.json() == {"tutor": t.id}


def test_sin_cookie_redirige(cliente, db):
    _operador(db)
    r = cliente.get("/_t")
    assert r.status_code == 303 and r.headers["location"] == "/entrar"
    assert cliente.get("/_a").headers["location"] == "/admin/entrar"


def test_operador_desconocido_404(cliente):
    assert cliente.get("/_t").status_code == 404


def test_sesion_de_otro_operador_no_vale(db, cliente):
    op = _operador(db)
    otro = _operador(db, host="otro.test")
    t = _tutor(db, otro)
    cliente.cookies.set("sb_sesion", crear_sesion(db, "tutor", t.id, otro.id))
    assert cliente.get("/_t").status_code == 303


def test_sesion_expirada_redirige(db, cliente):
    op = _operador(db)
    t = _tutor(db, op)
    cliente.cookies.set("sb_sesion", crear_sesion(db, "tutor", t.id, op.id))
    db.query(Sesion).update({"expira": datetime.now(timezone.utc) - timedelta(seconds=1)})
    db.commit()
    assert cliente.get("/_t").status_code == 303


def test_tipo_equivocado_no_vale(db, cliente):
    op = _operador(db)
    a = _admin(db, op)
    cliente.cookies.set("sb_sesion", crear_sesion(db, "admin", a.id, op.id))
    assert cliente.get("/_t").status_code == 303
    assert cliente.get("/_a").status_code == 200


def test_tutor_inactivo_no_vale(db, cliente):
    op = _operador(db)
    t = _tutor(db, op)
    cliente.cookies.set("sb_sesion", crear_sesion(db, "tutor", t.id, op.id))
    t.activo = False
    db.commit()
    assert cliente.get("/_t").status_code == 303


def test_cerrar_sesiones_respeta_la_actual(db):
    op = _operador(db)
    t = _tutor(db, op)
    a = crear_sesion(db, "tutor", t.id, op.id)
    crear_sesion(db, "tutor", t.id, op.id)
    cerrar_sesiones(db, "tutor", t.id, excepto_token=a)
    restantes = db.query(Sesion).all()
    assert len(restantes) == 1
    cerrar_sesiones(db, "tutor", t.id)
    assert db.query(Sesion).count() == 0


def test_csrf(db, cliente):
    op = _operador(db)
    t = _tutor(db, op)
    cliente.cookies.set("sb_sesion", crear_sesion(db, "tutor", t.id, op.id))
    csrf = db.query(Sesion).one().csrf
    assert cliente.post("/_p", data={"csrf": "mal"}).status_code == 403
    assert cliente.post("/_p", data={}).status_code == 403
    assert cliente.post("/_p", data={"csrf": csrf}).status_code == 200


def test_cookie_atributos():
    class S:
        cookie_secure = True
    r = Response()
    poner_cookie(r, "tok", S(), 60)
    c = r.headers["set-cookie"].lower()
    assert "httponly" in c and "secure" in c and "samesite=lax" in c and "max-age=60" in c
    S.cookie_secure = False
    r2 = Response()
    poner_cookie(r2, "tok", S(), 60)
    assert "secure" not in r2.headers["set-cookie"].lower()
    r3 = Response()
    borrar_cookie(r3)
    assert "sb_sesion" in r3.headers["set-cookie"]
