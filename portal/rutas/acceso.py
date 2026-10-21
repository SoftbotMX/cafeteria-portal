import hmac
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from portal.db import get_db
from portal.models import Operador, Sesion, Tutor
from portal.seguridad import (
    hash_contrasena, limite_excedido, normalizar_usuario, registrar_intento,
    validar_contrasena_nueva, verificar_contrasena,
)
from portal.sesiones import (
    COOKIE, DURACION, borrar_cookie, cerrar_sesion, cerrar_sesiones, crear_sesion,
    exigir_csrf, operador_actual, poner_cookie, sesion_tutor,
)
from portal.web import templates

router = APIRouter()

COOKIE_CSRF_LOGIN = "sb_csrf_login"
MSG_MALO = "Usuario o contraseña incorrectos"
MSG_EXPIRO = "Tu sesión expiró, vuelve a intentarlo."
# Clave de intentos para usuarios que no son teléfono ni correo. Las del cambio de
# contraseña ("cambio:<id>") nunca se pueden producir desde el login.
CLAVE_INVALIDA = "invalido"
MSG_LIMITE = "Demasiados intentos. Espera 15 minutos."
# Verificar contra un hash falso cuando el usuario no existe: mismo tiempo que con uno real.
_HASH_FALSO = hash_contrasena("sin-usuario-" + secrets.token_hex(8))


def _usuario_valido(clave: str) -> bool:
    return "@" in clave or (len(clave) == 10 and clave.isascii() and clave.isdigit())


def _ip(request: Request) -> str:
    return request.client.host if request.client else "desconocida"


def _pagina_entrar(request: Request, csrf: str, status: int = 200, error: str | None = None,
                   usuario: str = ""):
    resp = templates.TemplateResponse(
        request, "entrar.html", {"csrf": csrf, "error": error, "usuario": usuario},
        status_code=status)
    resp.set_cookie(COOKIE_CSRF_LOGIN, csrf, max_age=3600, httponly=True,
                    secure=request.app.state.settings.cookie_secure, samesite="lax", path="/")
    return resp


@router.get("/entrar")
def entrar_form(request: Request, _: Operador = Depends(operador_actual)):
    return _pagina_entrar(request, request.cookies.get(COOKIE_CSRF_LOGIN) or secrets.token_urlsafe(32))


@router.post("/entrar")
def entrar(request: Request, usuario: str = Form(""), contrasena: str = Form(""),
           csrf: str = Form(""), operador: Operador = Depends(operador_actual),
           db: Session = Depends(get_db)):
    # Doble envío: sin sesión previa, la cookie y el campo oculto deben coincidir.
    cookie = request.cookies.get(COOKIE_CSRF_LOGIN, "")
    try:
        exigir_csrf(csrf, cookie)
    except HTTPException:
        return _pagina_entrar(request, secrets.token_urlsafe(32), 403, MSG_EXPIRO, usuario)
    ip = _ip(request)
    clave = normalizar_usuario(usuario)
    valido = _usuario_valido(clave)
    # Un usuario inválido no escribe intentos bajo una clave arbitraria; cuenta solo por IP.
    clave_intento = clave if valido else CLAVE_INVALIDA
    if limite_excedido(db, operador.id, clave_intento, ip):
        return _pagina_entrar(request, cookie, 429, MSG_LIMITE, usuario)

    tutor = None
    if valido:
        campo = Tutor.correo if "@" in clave else Tutor.telefono
        tutor = db.scalar(select(Tutor).where(Tutor.operador_id == operador.id, campo == clave))
    ok = verificar_contrasena(tutor.contrasena_hash if tutor else _HASH_FALSO, contrasena)
    if tutor is None or not ok or not tutor.activo:
        registrar_intento(db, operador.id, clave_intento, ip, exito=False)
        db.commit()
        return _pagina_entrar(request, cookie, 200, MSG_MALO, usuario)

    registrar_intento(db, operador.id, clave, ip, exito=True)
    viejo = request.cookies.get(COOKIE)
    if viejo:
        cerrar_sesion(db, viejo)
    token = crear_sesion(db, "tutor", tutor.id, operador.id)
    db.commit()
    resp = RedirectResponse("/cambiar-contrasena" if tutor.debe_cambiar else "/", status_code=303)
    poner_cookie(resp, token, request.app.state.settings,
                 int(DURACION["tutor"].total_seconds()))
    resp.delete_cookie(COOKIE_CSRF_LOGIN, path="/")
    return resp


@router.post("/salir")
def salir(request: Request, csrf: str = Form(""),
          par: tuple[Sesion, Tutor] = Depends(sesion_tutor), db: Session = Depends(get_db)):
    exigir_csrf(csrf, par[0].csrf)
    cerrar_sesion(db, request.cookies[COOKIE])
    db.commit()
    resp = RedirectResponse("/entrar", status_code=303)
    borrar_cookie(resp)
    return resp


def _pagina_cambio(request: Request, sesion: Sesion, tutor: Tutor, status: int = 200,
                   error: str | None = None):
    return templates.TemplateResponse(
        request, "cambiar_contrasena.html",
        {"csrf": sesion.csrf, "error": error, "obligatorio": tutor.debe_cambiar},
        status_code=status)


@router.get("/cambiar-contrasena")
def cambiar_form(request: Request, par: tuple[Sesion, Tutor] = Depends(sesion_tutor)):
    return _pagina_cambio(request, *par)


@router.post("/cambiar-contrasena")
def cambiar(request: Request, actual: str = Form(""), nueva: str = Form(""),
            confirmar: str = Form(""), csrf: str = Form(""),
            par: tuple[Sesion, Tutor] = Depends(sesion_tutor), db: Session = Depends(get_db)):
    sesion, tutor = par
    try:
        exigir_csrf(csrf, sesion.csrf)
    except HTTPException:
        return _pagina_cambio(request, sesion, tutor, 403, MSG_EXPIRO)

    # La contraseña actual es adivinable con una sesión robada: mismo límite que el login.
    clave = f"cambio:{tutor.id}"
    if limite_excedido(db, tutor.operador_id, clave, _ip(request)):
        return _pagina_cambio(request, sesion, tutor, 429, MSG_LIMITE)
    if not verificar_contrasena(tutor.contrasena_hash, actual):
        registrar_intento(db, tutor.operador_id, clave, _ip(request), exito=False)
        db.commit()
        return _pagina_cambio(request, sesion, tutor, 400, "La contraseña actual no es correcta.")
    error = validar_contrasena_nueva(nueva)
    if error is None and nueva != confirmar:
        error = "La confirmación no coincide con la contraseña nueva."
    if error is None and nueva == actual:
        error = "La contraseña nueva debe ser distinta de la actual."
    if error:
        return _pagina_cambio(request, sesion, tutor, 400, error)

    tutor.contrasena_hash = hash_contrasena(nueva)
    tutor.debe_cambiar = False
    cerrar_sesiones(db, "tutor", tutor.id, excepto_token=request.cookies[COOKIE])
    db.commit()  # una sola transacción: o cambia la contraseña y se cierran las demás, o nada
    return RedirectResponse("/", status_code=303)
