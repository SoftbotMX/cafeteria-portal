from datetime import datetime, timedelta, timezone

from portal.models import IntentoLogin, Operador
from portal.seguridad import (
    generar_temporal, hash_contrasena, limite_excedido, normalizar_telefono,
    normalizar_usuario, registrar_intento, validar_contrasena_nueva, verificar_contrasena,
)


def _operador(db, host="a.test"):
    op = Operador(nombre="A", host=host, tpv_url="https://t", tpv_secreto_cif="x",
                  tpv_secreto_admin_cif="y")
    db.add(op)
    db.commit()
    return op


def test_normalizar_telefono():
    for t in ("(834) 144-5807", "+52 834 144 5807", "5218341445807", "8341445807"):
        assert normalizar_telefono(t) == "8341445807"
    assert normalizar_telefono("12345") is None
    assert normalizar_telefono("") is None


def test_normalizar_usuario_correo():
    assert normalizar_usuario(" Ana@X.MX ") == "ana@x.mx"


def test_normalizar_usuario_telefono_o_literal():
    assert normalizar_usuario("834 144 5807") == "8341445807"
    assert normalizar_usuario("admin") == "admin"


def test_hash_y_verificar():
    h = hash_contrasena("secreta123")
    assert verificar_contrasena(h, "secreta123") is True
    assert verificar_contrasena(h, "otra") is False
    assert verificar_contrasena("basura", "secreta123") is False


def test_temporal_sin_ambiguos():
    for _ in range(200):
        t = generar_temporal()
        assert len(t) == 10
        assert not set(t) & set("0O1lI")


def test_validar_contrasena_nueva():
    assert validar_contrasena_nueva("corta") is not None
    assert validar_contrasena_nueva("12345678") is None


def _fallos(db, op, usuario, ip, n, hace=timedelta(0)):
    for _ in range(n):
        db.add(IntentoLogin(operador_id=op.id, usuario=usuario, ip=ip, exito=False,
                            creado=datetime.now(timezone.utc) - hace))
    db.commit()


def test_limite_por_usuario(db):
    op = _operador(db)
    _fallos(db, op, "ana", "1.1.1.1", 4)
    assert limite_excedido(db, op.id, "ana", "9.9.9.9") is False
    _fallos(db, op, "ana", "2.2.2.2", 1)
    assert limite_excedido(db, op.id, "ana", "9.9.9.9") is True
    assert limite_excedido(db, op.id, "otro", "9.9.9.9") is False


def test_fallos_viejos_no_cuentan(db):
    op = _operador(db)
    _fallos(db, op, "ana", "1.1.1.1", 5, hace=timedelta(minutes=16))
    assert limite_excedido(db, op.id, "ana", "1.1.1.1") is False


def test_limite_por_ip(db):
    op = _operador(db)
    for i in range(19):
        _fallos(db, op, f"u{i}", "3.3.3.3", 1)
    assert limite_excedido(db, op.id, "nuevo", "3.3.3.3") is False
    _fallos(db, op, "u99", "3.3.3.3", 1)
    assert limite_excedido(db, op.id, "nuevo", "3.3.3.3") is True


def test_exitos_no_cuentan_y_registrar(db):
    op = _operador(db)
    for _ in range(6):
        registrar_intento(db, op.id, "ana", "1.1.1.1", True)
    assert limite_excedido(db, op.id, "ana", "1.1.1.1") is False
    for _ in range(5):
        registrar_intento(db, op.id, "ana", "1.1.1.1", False)
    assert limite_excedido(db, op.id, "ana", "1.1.1.1") is True
