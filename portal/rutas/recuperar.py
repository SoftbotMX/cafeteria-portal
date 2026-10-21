"""Recuperación de contraseña con un código de 6 dígitos de un solo uso.

Nunca revela si el usuario existe: todas las respuestas de /recuperar son iguales."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portal.credenciales import enviar_al_tutor
from portal.db import get_db
from portal.models import CodigoRecuperacion, IntentoLogin, Operador, Tutor
from portal.rutas.acceso import (
    COOKIE_CSRF_LOGIN, CLAVE_INVALIDA, MSG_EXPIRO, MSG_LIMITE, _usuario_valido,
    csrf_prelogin_valido, pagina_prelogin,
)
from portal.seguridad import (
    VENTANA_INTENTOS, bloquear_limites, hash_contrasena, invalidar_codigos, ip_cliente, limite_excedido, normalizar_usuario,
    registrar_intento, validar_contrasena_nueva,
)
from portal.sesiones import cerrar_sesiones, operador_actual

router = APIRouter()
log = logging.getLogger(__name__)

VIGENCIA = timedelta(minutes=15)
MAX_SOLICITUDES = 3
MAX_FALLOS_CODIGO = 5
MAX_FALLOS_TUTOR_24H = 10  # suma de intentos fallidos de todos sus códigos en 24 h
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


def _tutor_bloqueado(db: Session, tutor_id: int) -> bool:
    """Tope por tutor: 10 intentos fallidos en 24 h entre todos sus códigos."""
    total = db.scalar(select(func.coalesce(func.sum(CodigoRecuperacion.intentos), 0)).where(
        CodigoRecuperacion.tutor_id == tutor_id,
        CodigoRecuperacion.creado >= func.now() - timedelta(hours=24)))
    return total >= MAX_FALLOS_TUTOR_24H


def _enviar_codigo(app, operador_id: int, tutor_id: int, codigo: str, llave: str) -> None:
    """Tarea en segundo plano: así el tiempo de respuesta no depende de que el tutor exista.
    Recibe sólo valores simples y abre su propia sesión."""
    try:
        with Session(app.state.engine) as db:
            operador = db.get(Operador, operador_id)
            tutor = db.get(Tutor, tutor_id)
            canal, estado, _ = enviar_al_tutor(
                db, app, operador, tutor,
                f"Tu código para cambiar tu contraseña del portal de la cafetería es {codigo}. "
                "Vence en 15 minutos.", llave, asunto="Tu código para cambiar tu contraseña")
        nivel = logging.WARNING if estado in ("error", "sin_canal") else logging.INFO
        log.log(nivel, "Código de recuperación: tutor=%s canal=%s estado=%s", tutor_id, canal, estado)
    except Exception:  # el código nunca va al log
        log.exception("No se pudo enviar el código de recuperación (tutor %s)", tutor_id)


def _buscar_tutor(db: Session, operador: Operador, clave: str) -> Tutor | None:
    if not _usuario_valido(clave):
        return None
    campo = Tutor.correo if "@" in clave else Tutor.telefono
    return db.scalar(select(Tutor).where(Tutor.operador_id == operador.id, campo == clave))


@router.get("/recuperar")
def pedir_form(request: Request, _: Operador = Depends(operador_actual)):
    return _pagina_pedir(request, _csrf(request))


@router.post("/recuperar")
def pedir(request: Request, tareas: BackgroundTasks, usuario: str = Form(""), csrf: str = Form(""),
          operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    if not csrf_prelogin_valido(request, csrf):
        return _pagina_pedir(request, secrets.token_urlsafe(32), 403, MSG_EXPIRO, usuario)
    cookie = request.cookies[COOKIE_CSRF_LOGIN]
    ip = ip_cliente(request)
    clave = normalizar_usuario(usuario)
    valido = _usuario_valido(clave)
    clave_intento = P_SOLICITUD + (clave if valido else CLAVE_INVALIDA)
    # Contar y registrar es atómico: solicitudes simultáneas se turnan por clave y por IP.
    bloquear_limites(db, operador.id, clave_intento, "ip:" + ip)
    # Umbral propio (3), aparte del 5 del login; la IP sí comparte su límite de 20.
    if (valido and _solicitudes_recientes(db, operador.id, clave) >= MAX_SOLICITUDES) \
            or limite_excedido(db, operador.id, clave_intento, ip):
        db.rollback()
        return _pagina_pedir(request, cookie, 429, MSG_LIMITE, usuario)
    registrar_intento(db, operador.id, clave_intento, ip, exito=False)

    tutor = _buscar_tutor(db, operador, clave)
    nuevo = None
    codigo = None
    if tutor is not None and tutor.activo:
        # Serializa las emisiones del mismo tutor: queda un solo código vigente.
        db.refresh(tutor, with_for_update=True)
        if tutor.activo and not _tutor_bloqueado(db, tutor.id):
            codigo = f"{secrets.randbelow(10**6):06d}"
            invalidar_codigos(db, tutor.id)
            nuevo = CodigoRecuperacion(tutor_id=tutor.id, codigo_hash=_hash(codigo),
                                       expira=func.now() + VIGENCIA)
            db.add(nuevo)
            db.flush()
    llave = f"recuperar:{nuevo.id}" if nuevo else ""
    tutor_id = tutor.id if nuevo else None
    db.commit()  # antes de enviar: lo enviado siempre existe en la BD
    if nuevo is not None:
        tareas.add_task(_enviar_codigo, request.app, operador.id, tutor_id, codigo, llave)
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
    bloquear_limites(db, operador.id, clave_intento, "ip:" + ip)
    if limite_excedido(db, operador.id, clave_intento, ip):
        db.rollback()
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
        if not tutor.activo or _tutor_bloqueado(db, tutor.id):
            tutor = None
    if tutor is not None and tutor.activo:
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

    invalidar_codigos(db, tutor.id)
    tutor.contrasena_hash = hash_contrasena(nueva)
    tutor.debe_cambiar = False
    cerrar_sesiones(db, "tutor", tutor.id)
    db.commit()  # una sola transacción: contraseña, código gastado y sesiones cerradas
    return RedirectResponse("/entrar?cambiada=1", status_code=303)
