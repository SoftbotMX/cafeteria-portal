import pytest
from sqlalchemy import select, text

from portal import cli
from portal.config import get_settings
from portal.models import Admin, Operador
from portal.seguridad import verificar_contrasena
from tests.conftest import FERNET_PRUEBAS, URL_PRUEBAS


@pytest.fixture
def entorno(monkeypatch, db):
    monkeypatch.setenv("PORTAL_DATABASE_URL", URL_PRUEBAS)
    monkeypatch.setenv("PORTAL_FERNET_KEY", FERNET_PRUEBAS)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _operador_args():
    return ["crear-operador", "--nombre", "Cafe", "--host", "cafe.test",
            "--tpv-url", "https://tpv.test"]


def test_crear_operador_guarda_secretos_cifrados(entorno, db, monkeypatch):
    respuestas = iter(["sec-tutor", "sec-admin"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_: next(respuestas))
    cli.main(_operador_args())
    crudo = db.execute(text("SELECT tpv_secreto_cif, tpv_secreto_admin_cif FROM operador")).one()
    assert "sec-tutor" not in crudo[0] and "sec-admin" not in crudo[1]
    op = db.scalars(select(Operador)).one()
    assert op.secreto_tpv(FERNET_PRUEBAS) == "sec-tutor"
    assert op.secreto_tpv_admin(FERNET_PRUEBAS) == "sec-admin"
    assert op.secreto_conduit(FERNET_PRUEBAS) is None


def test_crear_operador_con_conduit(entorno, db, monkeypatch):
    respuestas = iter(["a", "b", "sec-conduit"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_: next(respuestas))
    cli.main(_operador_args() + ["--conduit-url", "https://c.test", "--conduit-client-id", "cid"])
    op = db.scalars(select(Operador)).one()
    assert op.conduit_client_id == "cid"
    assert op.secreto_conduit(FERNET_PRUEBAS) == "sec-conduit"


def test_crear_admin(entorno, db, monkeypatch):
    respuestas = iter(["a", "b"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_: next(respuestas))
    cli.main(_operador_args())
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_: "clave-larga-123")
    cli.main(["crear-admin", "--host", "cafe.test", "--usuario", "ana", "--nombre", "Ana"])
    adm = db.scalars(select(Admin)).one()
    assert adm.usuario == "ana" and adm.contrasena_hash != "clave-larga-123"
    assert verificar_contrasena(adm.contrasena_hash, "clave-larga-123")


def test_crear_admin_host_inexistente(entorno, monkeypatch):
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_: "x")
    with pytest.raises(SystemExit):
        cli.main(["crear-admin", "--host", "no.existe", "--usuario", "a", "--nombre", "A"])
