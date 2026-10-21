import re
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from portal.models import CodigoRecuperacion, IntentoLogin

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


def _usuario_bd(usuario: str) -> str:
    # Mismo recorte al escribir y al consultar, o el límite no vería los intentos largos.
    return usuario[:255]


def generar_temporal() -> str:
    return "".join(secrets.choice(_SIN_AMBIGUOS) for _ in range(10))


def normalizar_telefono(t: str) -> str | None:
    # Solo 0-9 ASCII: isdigit() acepta dígitos de ancho completo/árabes; esos se descartan y el número se rechaza.
    d = re.sub(r"[^0-9]", "", t)
    if len(d) == 12 and d.startswith("52"):
        d = d[-10:]
    elif len(d) == 13 and d.startswith("521"):
        d = d[-10:]
    return d if len(d) == 10 else None


def normalizar_usuario(u: str) -> str:
    u = u.strip()
    if "@" in u:
        return u.lower()
    return normalizar_telefono(u) or u


def validar_contrasena_nueva(c: str) -> str | None:
    if len(c) < 8:
        return "La contraseña debe tener al menos 8 caracteres."
    return None


def limite_excedido(db: Session, operador_id: int, usuario: str, ip: str) -> bool:
    """5 fallos por usuario o 20 por IP en 15 min. La IP es request.client.host:
    en P4 uvicorn debe correr con --proxy-headers tras nginx, o todos serían la IP del proxy."""
    desde = func.now() - VENTANA_INTENTOS  # reloj de la BD, el mismo que llena `creado`
    base = select(func.count()).select_from(IntentoLogin).where(
        IntentoLogin.operador_id == operador_id,
        IntentoLogin.exito.is_(False),
        IntentoLogin.creado >= desde,
    )
    por_usuario = db.scalar(base.where(IntentoLogin.usuario == _usuario_bd(usuario)))
    if por_usuario >= MAX_FALLOS_USUARIO:
        return True
    return db.scalar(base.where(IntentoLogin.ip == ip)) >= MAX_FALLOS_IP


def bloquear_limites(db: Session, operador_id: int, *claves: str) -> None:
    """Serializa las solicitudes que comparten clave (hasta el commit) para que contar y registrar
    el intento sea atómico. Orden fijo: dos solicitudes con las mismas claves no se bloquean entre sí."""
    for k in sorted({f"{operador_id}:{_usuario_bd(c)}" for c in claves}):
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": k})


def invalidar_codigos(db: Session, tutor_id: int) -> None:
    """Marca como usados los códigos de recuperación pendientes del tutor (flush, sin commit)."""
    db.execute(update(CodigoRecuperacion).where(
        CodigoRecuperacion.tutor_id == tutor_id, CodigoRecuperacion.usado.is_(False)
    ).values(usado=True))
    db.flush()


def registrar_intento(db: Session, operador_id: int, usuario: str, ip: str, exito: bool) -> None:
    db.add(IntentoLogin(operador_id=operador_id, usuario=_usuario_bd(usuario), ip=ip[:64], exito=exito))
    db.flush()


# Verificar contra un hash falso cuando el usuario no existe: mismo tiempo que con uno real.
HASH_FALSO = hash_contrasena("sin-usuario-" + secrets.token_hex(8))


def ip_cliente(request) -> str:
    return request.client.host if request.client else "desconocida"
