import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from portal.config import Settings
from portal.db import get_db
from portal.models import Admin, Operador, Sesion, Tutor

COOKIE = "sb_sesion"
DURACION = {"tutor": timedelta(days=30), "admin": timedelta(hours=12)}
_LOGIN = {"tutor": "/entrar", "admin": "/admin/entrar"}


class IrA(Exception):
    """Redirección 303; la registra create_app."""

    def __init__(self, url: str):
        self.url = url


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def crear_sesion(db: Session, tipo: str, usuario_id: int, operador_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(Sesion(
        token_hash=_hash(token), tipo=tipo, usuario_id=usuario_id, operador_id=operador_id,
        csrf=secrets.token_urlsafe(32), expira=datetime.now(timezone.utc) + DURACION[tipo],
    ))
    db.flush()
    return token


# Estas funciones solo hacen flush: la ruta confirma la transacción una vez al final.
def cerrar_sesiones(db: Session, tipo: str, usuario_id: int, excepto_token: str | None = None) -> None:
    q = delete(Sesion).where(Sesion.tipo == tipo, Sesion.usuario_id == usuario_id)
    if excepto_token:
        q = q.where(Sesion.token_hash != _hash(excepto_token))
    db.execute(q)
    db.flush()


def cerrar_sesion(db: Session, token: str) -> None:
    db.execute(delete(Sesion).where(Sesion.token_hash == _hash(token)))
    db.flush()


def poner_cookie(resp: Response, token: str, settings: Settings, max_age: int) -> None:
    resp.set_cookie(COOKIE, token, max_age=max_age, httponly=True,
                    secure=settings.cookie_secure, samesite="lax", path="/")


def cookie_csrf_login(settings: Settings) -> str:
    """Nombre de la cookie CSRF previa al login. Con https lleva __Host- (Secure, path=/, sin Domain)."""
    return ("__Host-" if settings.cookie_secure else "") + "sb_csrf_login"


def poner_csrf_login(resp: Response, csrf: str, settings: Settings) -> None:
    resp.set_cookie(cookie_csrf_login(settings), csrf, max_age=3600, httponly=True,
                    secure=settings.cookie_secure, samesite="lax", path="/")


def borrar_csrf_login(resp: Response, settings: Settings) -> None:
    resp.delete_cookie(cookie_csrf_login(settings), path="/", secure=settings.cookie_secure,
                       httponly=True, samesite="lax")


def borrar_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE, path="/")


def operador_actual(request: Request, db: Session = Depends(get_db)) -> Operador:
    op = db.scalar(select(Operador).where(Operador.host == request.url.hostname))
    if op is None:
        raise HTTPException(404)
    # Lo que pinta base.html en cada pantalla (nombre y logo de la cafetería), sin objetos de BD.
    request.state.cafeteria = {"nombre": op.nombre, "logo_version": op.logo_version}
    return op


def _sesion_valida(request: Request, db: Session, operador: Operador, tipo: str) -> Sesion:
    token = request.cookies.get(COOKIE)
    s = db.scalar(select(Sesion).where(Sesion.token_hash == _hash(token))) if token else None
    if (s is None or s.tipo != tipo or s.operador_id != operador.id
            or s.expira <= datetime.now(timezone.utc)):
        raise IrA(_LOGIN[tipo])
    return s


def sesion_tutor(request: Request, operador: Operador = Depends(operador_actual),
                 db: Session = Depends(get_db)) -> tuple[Sesion, Tutor]:
    s = _sesion_valida(request, db, operador, "tutor")
    tutor = db.get(Tutor, s.usuario_id)
    if tutor is None or tutor.operador_id != operador.id or not tutor.activo:
        raise IrA(_LOGIN["tutor"])
    return s, tutor


def tutor_listo(par: tuple[Sesion, Tutor] = Depends(sesion_tutor)) -> tuple[Sesion, Tutor]:
    """Sesión de tutor ya usable: mientras tenga contraseña temporal, solo puede cambiarla."""
    if par[1].debe_cambiar:
        raise IrA("/cambiar-contrasena")
    return par


def sesion_admin(request: Request, operador: Operador = Depends(operador_actual),
                 db: Session = Depends(get_db)) -> tuple[Sesion, Admin]:
    s = _sesion_valida(request, db, operador, "admin")
    admin = db.get(Admin, s.usuario_id)
    if admin is None or admin.operador_id != operador.id or not admin.activo:
        raise IrA(_LOGIN["admin"])
    return s, admin


def exigir_csrf(enviado: str | None, esperado: str) -> None:
    # Bytes: compare_digest con str no ASCII lanza TypeError (500).
    if not isinstance(enviado, str) or not esperado or not hmac.compare_digest(
            enviado.encode(), esperado.encode()):
        raise HTTPException(403, "Solicitud no válida. Recarga la página e inténtalo de nuevo.")


async def verificar_csrf(request: Request, sesion: Sesion) -> None:
    form = await request.form()
    exigir_csrf(form.get("csrf"), sesion.csrf)
