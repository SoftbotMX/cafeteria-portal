import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from sqlalchemy.orm import Session

from portal import tutor_tpv
from portal.db import get_db
from portal.models import Operador, Sesion, Tutor
from portal.reglas import ahora_local, ahora_utc, cargar_reglas, puede_cancelar
from portal.formato import TIPOS_CREDENCIAL
from portal.sesiones import exigir_csrf, operador_actual, tutor_listo
from portal.tutor_tpv import (
    LLAVE, MSG_SIN_PEDIDOS, MSG_SIN_TPV, SIN_CACHE, SinTPV, enviar_escritura, hijo_o_404,
    intento_incierto, msg_llave, ya_registrado)
from portal.web import templates

router = APIRouter()

POR_PAGINA = 20


@router.get("/")
def inicio(request: Request, par: tuple[Sesion, Tutor] = Depends(tutor_listo),
           operador: Operador = Depends(operador_actual)):
    sesion, tutor = par
    ctx = {"tutor": tutor, "csrf": sesion.csrf, "hijos": [], "error_tpv": None,
           "recargas": operador.recargas_activas}
    try:
        hijos = tutor_tpv.hijos(request, operador, tutor)
    except SinTPV:
        ctx["error_tpv"] = MSG_SIN_TPV
    else:
        if len(hijos) == 1 and isinstance(hijos[0].get("id"), int):
            return RedirectResponse(f"/hijos/{hijos[0]['id']}", status_code=303)
        ctx["hijos"] = hijos
    return templates.TemplateResponse(request, "tutor_inicio.html", ctx)


def _contexto_movs(request, operador, tutor, db, alumno_id: int, pagina: int) -> dict:
    datos = tutor_tpv.movimientos(request, operador, tutor, alumno_id, pagina, POR_PAGINA)
    return {"movs": datos["movimientos"], "alumno_id": alumno_id, "pagina": pagina,
            "hay_mas": pagina * POR_PAGINA < datos["total"], "error_tpv": None,
            "zona": cargar_reglas(db, operador.id).zona}


def _contexto_pedidos(request, operador, tutor, db, alumno_id: int, ahora: datetime) -> dict:
    """Próximos (de hoy en adelante) por día ascendente, luego los demás por día descendente.
    `cancelable`: sólo `retenida` y dentro de la hora límite."""
    pedidos = tutor_tpv.pedidos(request, operador, tutor, alumno_id)
    reglas = cargar_reglas(db, operador.id)
    hoy = ahora_local(reglas, ahora).date()
    for p in pedidos:
        p["cancelable"] = p.get("estado") == "retenida" and puede_cancelar(
            reglas, p["fecha_entrega"], ahora)
    proximos = sorted((p for p in pedidos if p["fecha_entrega"] >= hoy),
                      key=lambda p: (p["fecha_entrega"], p["id"]))
    previos = sorted((p for p in pedidos if p["fecha_entrega"] < hoy),
                     key=lambda p: (p["fecha_entrega"], p["id"]), reverse=True)
    return {"pedidos": proximos + previos, "error_pedidos": None}


@router.get("/hijos/{alumno_id}")
def hijo(alumno_id: int, request: Request, pagina: int = Query(1, ge=1, le=10000), par: tuple[Sesion, Tutor] = Depends(tutor_listo),
         operador: Operador = Depends(operador_actual), db: Session = Depends(get_db),
         ahora: datetime = Depends(ahora_utc)):
    sesion, tutor = par
    try:
        ficha, todos = tutor_tpv.hijo_o_404(request, operador, tutor, alumno_id)
    except SinTPV:
        return templates.TemplateResponse(request, "tutor/hijo.html", {
            "tutor": tutor, "csrf": sesion.csrf, "hijo": None, "error_tpv": MSG_SIN_TPV,
            "hay_varios": False})
    ctx = {"tutor": tutor, "csrf": sesion.csrf, "hijo": ficha, "hay_varios": len(todos) > 1,
           "recargas": operador.recargas_activas,
           "error_tpv": None}
    try:
        ctx.update(_contexto_pedidos(request, operador, tutor, db, alumno_id, ahora))
    except SinTPV:
        ctx.update(pedidos=[], error_pedidos=MSG_SIN_PEDIDOS)
    try:
        ctx.update(_contexto_movs(request, operador, tutor, db, alumno_id, pagina))
    except SinTPV:
        ctx.update(movs=[], hay_mas=False, error_mov=MSG_SIN_TPV, alumno_id=alumno_id,
                   pagina=pagina, zona=None)
    return templates.TemplateResponse(request, "tutor/hijo.html", ctx)


@router.get("/hijos/{alumno_id}/movimientos")
def movimientos(alumno_id: int, request: Request, pagina: int = Query(1, ge=1, le=10000),
                par: tuple[Sesion, Tutor] = Depends(tutor_listo),
                operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    _, tutor = par
    try:
        tutor_tpv.hijo_o_404(request, operador, tutor, alumno_id)
        ctx = _contexto_movs(request, operador, tutor, db, alumno_id, pagina)
    except SinTPV:
        ctx = {"movs": [], "hay_mas": False, "alumno_id": alumno_id, "pagina": pagina,
               "error_mov": MSG_SIN_TPV, "zona": None}
    return templates.TemplateResponse(request, "tutor/_movimientos.html", ctx)


# ------------------------------------------------- cambios de una sola escritura (Tasks 5 y 7)
# Permiso de comprar sin pedido y bloqueo de credenciales siguen el mismo camino: página de
# confirmación (con llave nueva) -> POST -> escritura al TPV con la misma llave si se reintenta.
# Ninguna mueve dinero. En la página de confirmación, pedir lo que ya está así no ofrece llave y dice «Ya estaba así», salvo que esa
# misma llave ya haya entrado (o quedado sin respuesta): entonces se repite la escritura, el TPV
# devuelve su respuesta guardada y el tutor ve el resultado en vez de un «ya estaba» engañoso.

MSG_SIN_RESPUESTA_CAMBIO = ("No se pudo contactar al TPV. Tu cambio puede haberse registrado: "
                            "vuelve a tocar el botón (no se repite) o revisa la ficha de tu hijo.")
MSG_INESPERADO = ("El TPV respondió algo inesperado. Revisa la ficha de {nombre} antes de volver "
                  "a intentarlo.")


@dataclass
class Cambio:
    operacion: str                       # operación del TPV y de la Bitácora
    titulo: str                          # de la confirmación
    titulo_error: str
    accion: str                          # URL del POST
    campos: dict                         # ocultos, sin `llave` ni `csrf`
    volver: str
    boton: str
    texto: str                           # lo que pasará (confirmación) y queda hecho (resultado)
    titulo_listo: str
    cuerpo: dict                         # al TPV, sin `llave` ni `tutor_ref`
    objetivo: bool
    leer: Callable[[dict], bool | None]  # estado actual dentro de una ficha de alumno
    titulo_fino: str = ""                # título en dos tonos de la confirmación (5q)
    titulo_fuerte: str = ""
    subtitulo: str = ""


def _pagina_tutor(request, plantilla, ctx, status=200):
    return templates.TemplateResponse(request, plantilla, ctx, status_code=status, headers=SIN_CACHE)


def _resultado(request, titulo, mensaje, volver, error=False, status=200):
    return _pagina_tutor(request, "tutor/resultado.html", {
        "titulo": titulo, "mensaje": mensaje, "volver": volver, "detalles": [], "error": error},
        status)


def _confirmacion(request, sesion, c: Cambio, llave: str, aviso=None, boton=None):
    return _pagina_tutor(request, "tutor/confirmar.html", {
        "titulo": c.titulo, "lineas": [c.texto], "aviso": aviso, "accion": c.accion,
        "csrf": sesion.csrf, "campos": {**c.campos, "llave": llave}, "boton": boton or c.boton,
        "volver": c.volver, "titulo_fino": c.titulo_fino, "titulo_fuerte": c.titulo_fuerte,
        "subtitulo": c.subtitulo})


def _ver_cambio(request, sesion, c: Cambio, hijo: dict, volver: str):
    """GET: nada que escribir si ya está así; si no, la confirmación con su llave."""
    if c.leer(hijo) is c.objetivo:
        return _resultado(request, c.titulo_listo, f"Ya estaba así: {c.texto}", volver)
    return _confirmacion(request, sesion, c, uuid.uuid4().hex)


def _hacer_cambio(request, sesion, tutor, operador, db, c: Cambio, hijo: dict, llave: str,
                  volver: str):
    """POST ya autorizado (CSRF y propiedad): validar llave, escribir y mostrar el resultado."""
    if not LLAVE.fullmatch(llave):
        return _resultado(request, c.titulo_error, msg_llave(c.operacion), volver, error=True)
    ya_esta = c.leer(hijo) is c.objetivo
    # Con esta llave pudo haber entrado ya (200, o un intento sin respuesta): el TPV puede
    # contestar con su respuesta GUARDADA sin aplicar nada, aunque otro tutor lo haya cambiado después.
    entro = ya_registrado(db, operador, tutor, llave, c.operacion) or intento_incierto(
        db, operador, tutor, llave, c.operacion)
    # Aunque ya esté así se escribe igual (el TPV no cambia nada y GUARDA la respuesta): así la
    # llave queda consumida y una página vieja repetida después no deshace un cambio posterior
    # (p. ej. desbloquear una tarjeta que otro tutor bloqueó por pérdida). Hallazgo de Codex.
    estado, r = enviar_escritura(
        db, request, operador, tutor, c.operacion, {**c.cuerpo, "llave": llave}, llave, hijo,
        "volver a intentarlo", "la ficha")
    if estado == "incierto":
        return _confirmacion(request, sesion, c, llave, MSG_SIN_RESPUESTA_CAMBIO)
    if estado == "rechazo":
        return _resultado(request, c.titulo_error, r, volver, error=True)
    alumno = r.get("alumno") if isinstance(r.get("alumno"), dict) else {}
    if c.leer(alumno) is not c.objetivo:
        return _resultado(request, c.titulo_error, MSG_INESPERADO.format(nombre=hijo["nombre"]),
                          volver, error=True)
    if entro:
        # La respuesta puede ser la guardada: el estado de verdad es el de la ficha de AHORA.
        try:
            actual = c.leer(hijo_o_404(request, operador, tutor, hijo["id"])[0])
        except (SinTPV, HTTPException):
            actual = None
        if actual is not c.objetivo:
            return _resultado(request, c.titulo_listo,
                              "Tu cambio se registró, pero la ficha de "
                              f"{hijo['nombre']} ya no está así: puede que el otro padre de familia lo haya cambiado. "
                              "Revísala.", volver)
    if ya_esta and not entro:
        return _resultado(request, c.titulo_listo, f"Ya estaba así: {c.texto}", volver)
    return _resultado(request, c.titulo_listo, c.texto, volver)


def _sin_tpv(request, mensaje, volver, titulo):
    return _resultado(request, titulo, mensaje, volver, error=True)


# --- permiso de comprar sin pedido

OP_PERMISO = "alumno/permiso"
TIT_PERMISO = "No se pudo cambiar el permiso"
VALORES_PERMISO = {"si": True, "no": False}


def _leer_permiso(alumno: dict) -> bool | None:
    v = alumno.get("compra_sin_pedido")
    return v if isinstance(v, bool) else None


def _cambio_permiso(hijo: dict, valor: str) -> Cambio:
    nuevo = VALORES_PERMISO[valor]
    nombre = hijo["nombre"]
    texto = (f"{nombre} podrá comprar sin pedido" if nuevo
             else f"{nombre} sólo podrá recoger lo que pidas")
    return Cambio(
        operacion=OP_PERMISO, titulo=f"Compras sin pedido de {nombre}", titulo_error=TIT_PERMISO,
        accion=f"/hijos/{hijo['id']}/permiso", campos={"valor": valor},
        volver=f"/hijos/{hijo['id']}",
        boton="Confirmar", texto=texto, titulo_listo="Permiso actualizado",
        cuerpo={"alumno_id": hijo["id"], "compra_sin_pedido": nuevo},
        objetivo=nuevo, leer=_leer_permiso,
        titulo_fino="Compras", titulo_fuerte="sin pedido", subtitulo=f"de {nombre}")


def _hijo_para_cambio(request, operador, tutor, alumno_id, volver, titulo):
    """(ficha, None) o (None, respuesta de error si el TPV no contesta); 404 si no es su hijo."""
    try:
        return hijo_o_404(request, operador, tutor, alumno_id)[0], None
    except SinTPV:
        return None, _sin_tpv(request, MSG_SIN_TPV, volver, titulo)


@router.get("/hijos/{alumno_id}/permiso")
def permiso_confirmacion(alumno_id: int, request: Request, valor: str = Query("", max_length=20),
                         par: tuple[Sesion, Tutor] = Depends(tutor_listo),
                         operador: Operador = Depends(operador_actual)):
    sesion, tutor = par
    volver = f"/hijos/{alumno_id}"
    hijo, error = _hijo_para_cambio(request, operador, tutor, alumno_id, volver, TIT_PERMISO)
    if error:
        return error
    if valor not in VALORES_PERMISO:
        return _resultado(request, TIT_PERMISO, "Valor no válido.", volver, error=True, status=400)
    return _ver_cambio(request, sesion, _cambio_permiso(hijo, valor), hijo, volver)


@router.post("/hijos/{alumno_id}/permiso")
def permiso(alumno_id: int, request: Request, csrf: str = Form(""), valor: str = Form(""),
            llave: str = Form(""), par: tuple[Sesion, Tutor] = Depends(tutor_listo),
            operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, tutor = par
    exigir_csrf(csrf, sesion.csrf)
    volver = f"/hijos/{alumno_id}"
    hijo, error = _hijo_para_cambio(request, operador, tutor, alumno_id, volver, TIT_PERMISO)
    if error:
        return error
    if valor not in VALORES_PERMISO:
        return _resultado(request, TIT_PERMISO, "Valor no válido.", volver, error=True, status=400)
    return _hacer_cambio(request, sesion, tutor, operador, db, _cambio_permiso(hijo, valor), hijo,
                         llave, volver)


# --- bloquear y desbloquear credenciales

ACCIONES_CREDENCIAL = {"bloquear": True, "desbloquear": False}  # acción -> `bloqueada` resultante
TIT_CREDENCIAL = "No se pudo cambiar la credencial"
# Con artículo: «esta tarjeta», «este código QR» (el género no se puede sacar de la etiqueta).
ESTA_CREDENCIAL = {"nfc": "esta tarjeta", "qr": "este código QR"}


def _credencial_de(alumno: dict, cred_id: int) -> dict | None:
    for x in alumno.get("credenciales") or []:
        if isinstance(x, dict) and x.get("id") == cred_id and not isinstance(x.get("id"), bool):
            return x
    return None


def _cambio_credencial(hijo: dict, cred: dict, accion: str) -> Cambio:
    cred_id, tipo = cred["id"], cred.get("tipo")
    esta = ESTA_CREDENCIAL.get(tipo, "esta credencial")
    bloquear = ACCIONES_CREDENCIAL[accion]

    def leer(alumno: dict) -> bool | None:
        x = _credencial_de(alumno, cred_id)
        return x["bloqueada"] if x and isinstance(x.get("bloqueada"), bool) else None

    etiqueta = TIPOS_CREDENCIAL.get(tipo, "Credencial")
    return Cambio(
        operacion=f"credencial/{accion}",
        titulo=f"{'Bloquear' if bloquear else 'Desbloquear'}: {etiqueta} de {hijo['nombre']}",
        titulo_error=TIT_CREDENCIAL, accion=f"/hijos/{hijo['id']}/credenciales/{cred_id}/{accion}",
        campos={}, volver=f"/hijos/{hijo['id']}", boton="Bloquear" if bloquear else "Desbloquear",
        texto=(f"Nadie podrá pagar con {esta} hasta que la desbloquees." if bloquear
               else f"Se podrá volver a pagar con {esta}."),
        titulo_listo=f"{etiqueta} {'bloqueada' if bloquear else 'desbloqueada'}",
        cuerpo={"credencial_id": cred_id}, objetivo=bloquear,
        leer=leer, titulo_fino="Bloquear" if bloquear else "Desbloquear", titulo_fuerte=etiqueta,
        subtitulo=f"de {hijo['nombre']}")


def _credencial_del_hijo(hijo: dict, cred_id: int) -> dict:
    """Sólo vale una credencial que la ficha de ESTE hijo lista (las dadas de baja no salen):
    si no, 404 sin tocar el TPV."""
    cred = _credencial_de(hijo, cred_id)
    if cred is None:
        raise HTTPException(404)
    return cred


@router.get("/hijos/{alumno_id}/credenciales/{cred_id}/{accion}")
def credencial_confirmacion(alumno_id: int, cred_id: int, accion: str, request: Request,
                            par: tuple[Sesion, Tutor] = Depends(tutor_listo),
                            operador: Operador = Depends(operador_actual)):
    if accion not in ACCIONES_CREDENCIAL:
        raise HTTPException(404)
    sesion, tutor = par
    volver = f"/hijos/{alumno_id}"
    hijo, error = _hijo_para_cambio(request, operador, tutor, alumno_id, volver, TIT_CREDENCIAL)
    if error:
        return error
    cred = _credencial_del_hijo(hijo, cred_id)
    return _ver_cambio(request, sesion, _cambio_credencial(hijo, cred, accion), hijo, volver)


@router.post("/hijos/{alumno_id}/credenciales/{cred_id}/{accion}")
def credencial(alumno_id: int, cred_id: int, accion: str, request: Request, csrf: str = Form(""),
               llave: str = Form(""), par: tuple[Sesion, Tutor] = Depends(tutor_listo),
               operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    if accion not in ACCIONES_CREDENCIAL:
        raise HTTPException(404)
    sesion, tutor = par
    exigir_csrf(csrf, sesion.csrf)
    volver = f"/hijos/{alumno_id}"
    hijo, error = _hijo_para_cambio(request, operador, tutor, alumno_id, volver, TIT_CREDENCIAL)
    if error:
        return error
    cred = _credencial_del_hijo(hijo, cred_id)
    return _hacer_cambio(request, sesion, tutor, operador, db, _cambio_credencial(hijo, cred, accion),
                         hijo, llave, volver)
