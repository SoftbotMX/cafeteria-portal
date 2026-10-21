import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from portal.db import get_db
from portal.models import Operador, Sesion, Tutor
from portal.seguridad import (
    HASH_FALSO, bloquear_limites, hash_contrasena, invalidar_codigos, ip_cliente, limite_excedido, normalizar_usuario, registrar_intento,
    validar_contrasena_nueva, verificar_contrasena,
)
from portal.sesiones import (
    COOKIE, DURACION, borrar_cookie, borrar_csrf_login, cookie_csrf_login, poner_csrf_login, cerrar_sesion, cerrar_sesiones, crear_sesion,
    exigir_csrf, operador_actual, poner_cookie, sesion_tutor,
)
from portal.web import templates

router = APIRouter()

MSG_MALO = "Usuario o contraseña incorrectos"
MSG_EXPIRO = "Tu sesión expiró, vuelve a intentarlo."
# Clave de intentos para usuarios que no son teléfono ni correo. Las del cambio de
# contraseña ("cambio:<id>") nunca se pueden producir desde el login.
CLAVE_INVALIDA = "invalido"
MSG_LIMITE = "Demasiados intentos. Espera 15 minutos."


def _usuario_valido(clave: str) -> bool:
    # Sin ":" en el correo: las claves de intentos usan prefijos "admin:", "recuperar:", "cambio:"
    # y un usuario no puede fabricarlos.
    if ":" in clave:
        return False
    return "@" in clave or (len(clave) == 10 and clave.isascii() and clave.isdigit())


def pagina_prelogin(request: Request, plantilla: str, csrf: str, status: int = 200, **contexto):
    """Página previa a la sesión: lleva el CSRF de doble envío (campo oculto + cookie)."""
    resp = templates.TemplateResponse(
        request, plantilla, {"csrf": csrf, **contexto}, status_code=status)
    poner_csrf_login(resp, csrf, request.app.state.settings)
    return resp


def csrf_prelogin_valido(request: Request, enviado: str) -> bool:
    try:
        exigir_csrf(enviado, request.cookies.get(cookie_csrf_login(request.app.state.settings), ""))
    except HTTPException:
        return False
    return True


def _pagina_entrar(request: Request, csrf: str, status: int = 200, error: str | None = None,
                   usuario: str = "", aviso: str | None = None):
    return pagina_prelogin(request, "entrar.html", csrf, status, error=error, usuario=usuario,
                           aviso=aviso)


@router.get("/entrar")
def entrar_form(request: Request, cambiada: str = "", _: Operador = Depends(operador_actual)):
    aviso = "Tu contraseña cambió. Ya puedes entrar." if cambiada == "1" else None
    return _pagina_entrar(request, request.cookies.get(cookie_csrf_login(request.app.state.settings)) or secrets.token_urlsafe(32),
                          aviso=aviso)


@router.post("/entrar")
def entrar(request: Request, usuario: str = Form(""), contrasena: str = Form(""),
           csrf: str = Form(""), operador: Operador = Depends(operador_actual),
           db: Session = Depends(get_db)):
    # Doble envío: sin sesión previa, la cookie y el campo oculto deben coincidir.
    cookie = request.cookies.get(cookie_csrf_login(request.app.state.settings), "")
    if not csrf_prelogin_valido(request, csrf):
        return _pagina_entrar(request, secrets.token_urlsafe(32), 403, MSG_EXPIRO, usuario)
    ip = ip_cliente(request)
    clave = normalizar_usuario(usuario)
    valido = _usuario_valido(clave)
    # Un usuario inválido no escribe intentos bajo una clave arbitraria; cuenta solo por IP.
    clave_intento = clave if valido else CLAVE_INVALIDA
    # Contar, verificar y registrar es atómico por usuario: una ráfaga no verifica más de 5 contraseñas.
    # (El tope por IP queda blando ante la concurrencia; bloquear la IP serializaría el wifi de la escuela.)
    bloquear_limites(db, operador.id, clave_intento)
    if limite_excedido(db, operador.id, clave_intento, ip):
        db.rollback()
        return _pagina_entrar(request, cookie, 429, MSG_LIMITE, usuario)

    tutor = None
    if valido:
        campo = Tutor.correo if "@" in clave else Tutor.telefono
        tutor = db.scalar(select(Tutor).where(Tutor.operador_id == operador.id, campo == clave))
    hash_visto = tutor.contrasena_hash if tutor else HASH_FALSO
    ok = verificar_contrasena(hash_visto, contrasena)
    if ok and tutor is not None:
        # Candado del tutor y relectura: si una recuperación o un reenvío cambió la contraseña (y cerró
        # sus sesiones) mientras se verificaba, esta verificación ya no vale y no se crea sesión.
        db.refresh(tutor, with_for_update=True)
        ok = tutor.contrasena_hash == hash_visto
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
    borrar_csrf_login(resp, request.app.state.settings)
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
    bloquear_limites(db, tutor.operador_id, clave)
    if limite_excedido(db, tutor.operador_id, clave, ip_cliente(request)):
        db.rollback()
        return _pagina_cambio(request, sesion, tutor, 429, MSG_LIMITE)
    if not verificar_contrasena(tutor.contrasena_hash, actual):
        registrar_intento(db, tutor.operador_id, clave, ip_cliente(request), exito=False)
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
    invalidar_codigos(db, tutor.id)
    cerrar_sesiones(db, "tutor", tutor.id, excepto_token=request.cookies[COOKIE])
    db.commit()  # una sola transacción: o cambia la contraseña y se cierran las demás, o nada
    return RedirectResponse("/", status_code=303)
