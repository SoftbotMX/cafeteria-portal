"""Recuperación de contraseña con un código de 6 dígitos de un solo uso.

Nunca revela si el usuario existe: todas las respuestas de /recuperar son iguales."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from portal.credenciales import enviar_al_tutor
from portal.db import get_db
from portal.models import CodigoRecuperacion, IntentoLogin, Operador, Tutor
from portal.rutas.acceso import (
    COOKIE_CSRF_LOGIN, CLAVE_INVALIDA, MSG_EXPIRO, MSG_LIMITE, _usuario_valido,
    csrf_prelogin_valido, pagina_prelogin,
)
from portal.seguridad import (
    VENTANA_INTENTOS, hash_contrasena, ip_cliente, limite_excedido, normalizar_usuario,
    registrar_intento, validar_contrasena_nueva,
)
from portal.sesiones import cerrar_sesiones, operador_actual

router = APIRouter()
log = logging.getLogger(__name__)

VIGENCIA = timedelta(minutes=15)
MAX_SOLICITUDES = 3
MAX_FALLOS_CODIGO = 5
MENSAJE = "Si el usuario existe, te mandamos un código"
MSG_CODIGO_MALO = "El código es incorrecto o ya venció."
# Prefijos que el login nunca produce (sus claves son teléfono, correo o "invalido").
P_SOLICITUD = "recuperar:"
P_CODIGO = "recuperar-codigo:"


def _hash(codigo: str) -> str:
    return hashlib.sha256(codigo.encode()).hexdigest()


def _csrf(request: Request) -> str:
    return request.cookies.get(COOKIE_CSRF_LOGIN) or secrets.token_urlsafe(32)


def _pagina_pedir(request, csrf, status=200, error=None, usuario=""):
    return pagina_prelogin(request, "recuperar.html", csrf, status, error=error, usuario=usuario,
                           mensaje=MENSAJE)


def _pagina_codigo(request, csrf, status=200, error=None, usuario=""):
    return pagina_prelogin(request, "recuperar_codigo.html", csrf, status, error=error,
                           usuario=usuario, mensaje=MENSAJE)


def _solicitudes_recientes(db: Session, operador_id: int, clave: str) -> int:
    return db.scalar(select(func.count()).select_from(IntentoLogin).where(
        IntentoLogin.operador_id == operador_id, IntentoLogin.usuario == P_SOLICITUD + clave,
        IntentoLogin.creado >= func.now() - VENTANA_INTENTOS)) or 0


def _buscar_tutor(db: Session, operador: Operador, clave: str) -> Tutor | None:
    if not _usuario_valido(clave):
        return None
    campo = Tutor.correo if "@" in clave else Tutor.telefono
    return db.scalar(select(Tutor).where(Tutor.operador_id == operador.id, campo == clave))


@router.get("/recuperar")
def pedir_form(request: Request, _: Operador = Depends(operador_actual)):
    return _pagina_pedir(request, _csrf(request))


@router.post("/recuperar")
def pedir(request: Request, usuario: str = Form(""), csrf: str = Form(""),
          operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    if not csrf_prelogin_valido(request, csrf):
        return _pagina_pedir(request, secrets.token_urlsafe(32), 403, MSG_EXPIRO, usuario)
    cookie = request.cookies[COOKIE_CSRF_LOGIN]
    ip = ip_cliente(request)
    clave = normalizar_usuario(usuario)
    valido = _usuario_valido(clave)
    clave_intento = P_SOLICITUD + (clave if valido else CLAVE_INVALIDA)
    # Umbral propio (3), aparte del 5 del login; la IP sí comparte su límite de 20.
    if (valido and _solicitudes_recientes(db, operador.id, clave) >= MAX_SOLICITUDES) \
            or limite_excedido(db, operador.id, clave_intento, ip):
        return _pagina_pedir(request, cookie, 429, MSG_LIMITE, usuario)
    registrar_intento(db, operador.id, clave_intento, ip, exito=False)

    tutor = _buscar_tutor(db, operador, clave)
    nuevo = None
    codigo = None
    if tutor is not None and tutor.activo:
        # Serializa las emisiones del mismo tutor: queda un solo código vigente.
        db.refresh(tutor, with_for_update=True)
        codigo = f"{secrets.randbelow(10**6):06d}"
        db.execute(update(CodigoRecuperacion).where(
            CodigoRecuperacion.tutor_id == tutor.id, CodigoRecuperacion.usado.is_(False)
        ).values(usado=True))
        nuevo = CodigoRecuperacion(tutor_id=tutor.id, codigo_hash=_hash(codigo),
                                   expira=func.now() + VIGENCIA)
        db.add(nuevo)
        db.flush()
    llave = f"recuperar:{nuevo.id}" if nuevo else ""
    db.commit()  # antes de enviar: lo enviado siempre existe en la BD
    if nuevo is not None:
        try:
            enviar_al_tutor(
                db, request.app, operador, tutor,
                f"Tu código para cambiar tu contraseña del portal de la cafetería es {codigo}. "
                "Vence en 15 minutos.", llave)
        except Exception:  # la respuesta no debe delatar nada; el código nunca va al log
            log.exception("No se pudo enviar el código de recuperación (tutor %s)", tutor.id)
    return RedirectResponse(f"/recuperar/codigo?u={quote(clave, safe='@')}", status_code=303)


@router.get("/recuperar/codigo")
def codigo_form(request: Request, u: str = "", _: Operador = Depends(operador_actual)):
    return _pagina_codigo(request, _csrf(request), usuario=u[:255])


@router.post("/recuperar/codigo")
def codigo(request: Request, usuario: str = Form(""), codigo: str = Form(""),
           nueva: str = Form(""), confirmar: str = Form(""), csrf: str = Form(""),
           operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    if not csrf_prelogin_valido(request, csrf):
        return _pagina_codigo(request, secrets.token_urlsafe(32), 403, MSG_EXPIRO, usuario)
    cookie = request.cookies[COOKIE_CSRF_LOGIN]
    ip = ip_cliente(request)
    clave = normalizar_usuario(usuario)
    clave_intento = P_CODIGO + (clave if _usuario_valido(clave) else CLAVE_INVALIDA)
    if limite_excedido(db, operador.id, clave_intento, ip):
        return _pagina_codigo(request, cookie, 429, MSG_LIMITE, usuario)

    # Antes de mirar el código: así un error de formato no revela si el código era bueno.
    error = validar_contrasena_nueva(nueva)
    if error is None and nueva != confirmar:
        error = "La confirmación no coincide con la contraseña nueva."
    if error:
        return _pagina_codigo(request, cookie, 400, error, usuario)

    tutor = _buscar_tutor(db, operador, clave)
    fila = None
    if tutor is not None and tutor.activo:
        db.refresh(tutor, with_for_update=True)  # mismo orden de bloqueos que pedir(): tutor, luego código
        # FOR UPDATE: dos envíos simultáneos no pueden usar el mismo código ni saltarse el conteo.
        fila = db.scalar(select(CodigoRecuperacion).where(
            CodigoRecuperacion.tutor_id == tutor.id, CodigoRecuperacion.usado.is_(False),
            CodigoRecuperacion.intentos < MAX_FALLOS_CODIGO, CodigoRecuperacion.expira > func.now(),
        ).order_by(CodigoRecuperacion.id.desc()).limit(1).with_for_update())
    ok = fila is not None and hmac.compare_digest(_hash(codigo.strip()), fila.codigo_hash)
    if not ok:
        if fila is not None:
            fila.intentos += 1
            if fila.intentos >= MAX_FALLOS_CODIGO:
                fila.usado = True
        registrar_intento(db, operador.id, clave_intento, ip, exito=False)
        db.commit()
        return _pagina_codigo(request, cookie, 400, MSG_CODIGO_MALO, usuario)

    db.execute(update(CodigoRecuperacion).where(
        CodigoRecuperacion.tutor_id == tutor.id, CodigoRecuperacion.usado.is_(False)
    ).values(usado=True))
    tutor.contrasena_hash = hash_contrasena(nueva)
    tutor.debe_cambiar = False
    cerrar_sesiones(db, "tutor", tutor.id)
    db.commit()  # una sola transacción: contraseña, código gastado y sesiones cerradas
    return RedirectResponse("/entrar?cambiada=1", status_code=303)
