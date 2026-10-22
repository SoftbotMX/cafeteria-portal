from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from portal import cli
from portal.models import Operador, Recarga
from tests.conftest import FERNET_PRUEBAS
from tests.fabricas import crear_operador, crear_tutor
from tests.test_cli import entorno  # noqa: F401  (fixture)


def _recarga(db, op, tutor, llave="L1", **kw):
    r = Recarga(operador_id=op.id, tutor_id=tutor.id, llave=llave, total=Decimal("150"),
                reparto=[{"alumno_id": 1, "nombre": "Luis", "importe": 150}], **kw)
    db.add(r)
    db.commit()
    return r


def test_recarga_nace_pendiente(db):
    op = crear_operador(db)
    r = _recarga(db, op, crear_tutor(db, op))
    assert r.estado == "pendiente" and r.total == Decimal("150.00") and r.mp_pago_id is None


def test_llave_unica_por_tutor(db):
    op = crear_operador(db)
    t = crear_tutor(db, op)
    _recarga(db, op, t)
    with pytest.raises(IntegrityError):
        _recarga(db, op, t)


def test_un_pago_de_mp_no_acredita_dos_recargas(db):
    op = crear_operador(db)
    t = crear_tutor(db, op)
    _recarga(db, op, t, llave="A", mp_pago_id="123")
    _recarga(db, op, t, llave="B")  # sin pago: no choca
    _recarga(db, op, t, llave="C")
    with pytest.raises(IntegrityError):
        _recarga(db, op, t, llave="D", mp_pago_id="123")


def test_token_mp_cifrado(db):
    op = crear_operador(db)
    assert op.token_mp(FERNET_PRUEBAS) is None


def test_cli_guardar_mp_cifra_el_token(entorno, db, monkeypatch):  # noqa: F811
    crear_operador(db, host="cafe.test")
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_: "APP_USR-token-de-prueba")
    cli.main(["guardar-mp", "--host", "cafe.test"])
    db.expire_all()
    crudo = db.execute(text("SELECT mp_access_token_cif FROM operador")).scalar()
    assert "APP_USR" not in crudo
    assert db.scalars(select(Operador)).one().token_mp(FERNET_PRUEBAS) == "APP_USR-token-de-prueba"


def test_cli_guardar_mp_sin_operador(entorno, db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_: "x")
    with pytest.raises(SystemExit):
        cli.main(["guardar-mp", "--host", "no.existe"])
