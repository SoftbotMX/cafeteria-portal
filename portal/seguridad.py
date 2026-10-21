import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portal.models import IntentoLogin

_hasher = PasswordHasher()


def hash_contrasena(c: str) -> str:
    return _hasher.hash(c)


def verificar_contrasena(hash: str, c: str) -> bool:
    try:
        return _hasher.verify(hash, c)
    except (VerifyMismatchError, InvalidHashError):
        return False


_SIN_AMBIGUOS = "abcdefghjkmnpqrstuvwxyz23456789"
VENTANA_INTENTOS = timedelta(minutes=15)
MAX_FALLOS_USUARIO = 5
MAX_FALLOS_IP = 20


def generar_temporal() -> str:
    return "".join(secrets.choice(_SIN_AMBIGUOS) for _ in range(10))


def normalizar_telefono(t: str) -> str | None:
    d = "".join(ch for ch in t if ch.isdigit())
    if len(d) == 12 and d.startswith("52"):
        d = d[-10:]
    elif len(d) == 13 and d.startswith("521"):
        d = d[-10:]
    return d if len(d) == 10 else None


def normalizar_usuario(u: str) -> str:
    if "@" in u:
        return u.strip().lower()
    return normalizar_telefono(u) or u


def validar_contrasena_nueva(c: str) -> str | None:
    if len(c) < 8:
        return "La contraseña debe tener al menos 8 caracteres."
    return None


def limite_excedido(db: Session, operador_id: int, usuario: str, ip: str) -> bool:
    """5 fallos por usuario o 20 por IP en 15 min. La IP es request.client.host:
    en P4 uvicorn debe correr con --proxy-headers tras nginx, o todos serían la IP del proxy."""
    desde = datetime.now(timezone.utc) - VENTANA_INTENTOS
    base = select(func.count()).select_from(IntentoLogin).where(
        IntentoLogin.operador_id == operador_id,
        IntentoLogin.exito.is_(False),
        IntentoLogin.creado >= desde,
    )
    por_usuario = db.scalar(base.where(IntentoLogin.usuario == usuario))
    if por_usuario >= MAX_FALLOS_USUARIO:
        return True
    return db.scalar(base.where(IntentoLogin.ip == ip)) >= MAX_FALLOS_IP


def registrar_intento(db: Session, operador_id: int, usuario: str, ip: str, exito: bool) -> None:
    db.add(IntentoLogin(operador_id=operador_id, usuario=usuario[:255], ip=ip[:64], exito=exito))
    db.commit()
