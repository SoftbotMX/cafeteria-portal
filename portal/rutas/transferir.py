"""Transferir saldo disponible de un hijo a otro (operación `transferencia` del TPV).

El portal no guarda saldos: el disponible de cada hijo se lee del TPV en cada paso, y los
saldos nuevos que se muestran son los de la respuesta del TPV, nunca una resta local.
"""
import re
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from portal import tutor_tpv
from portal.db import get_db
from portal.formato import _cantidad, dinero, parse_importe
from portal.models import Operador, Sesion, Tutor
from portal.sesiones import exigir_csrf, operador_actual, tutor_listo
from portal.tutor_tpv import (
    LLAVE, MSG_SIN_TPV, SIN_CACHE, SinTPV, con_aviso, enviar_escritura, intento_incierto,
    msg_llave, ya_registrado)
from portal.web import templates

router = APIRouter()

OP = "transferencia"
_ID = re.compile(r"[0-9]{1,12}")
MSG_IMPORTE = "Escribe un importe válido, mayor que cero y con máximo 2 decimales (por ejemplo 150.50)."
MSG_DISTINTOS = "Elige dos hijos distintos: de quién sale el saldo y a quién llega."
MSG_ELIGE = "Elige de quién sale el saldo y a quién llega."
MSG_SIN_RESPUESTA = ("No se pudo contactar al TPV. Tu transferencia puede haberse registrado: vuelve a "
                     "tocar «Confirmar transferencia» (no se duplica) o revisa los movimientos de tus hijos.")
MSG_INESPERADO = ("El TPV respondió algo inesperado. Revisa los movimientos de {} antes de volver a "
                  "transferir.")
TITULO_ERROR = "No se pudo transferir el saldo"
TITULO_INCIERTO = "No sabemos si se transfirió el saldo"


def _pagina(request, plantilla, ctx, status=200):
    return templates.TemplateResponse(request, plantilla, ctx, status_code=status, headers=SIN_CACHE)


def _error(request, mensaje: str, volver: str = "/transferir", titulo: str = TITULO_ERROR):
    return _pagina(request, "tutor/resultado.html", {
        "titulo": titulo, "mensaje": mensaje, "volver": volver, "detalles": [],
        "error": True})


def _formulario(request, sesion, hijos, mensaje=None, origen="", destino="", importe=""):
    return _pagina(request, "tutor/transferir.html", {
        "hijos": hijos, "csrf": sesion.csrf, "error": mensaje,
        "origen": str(origen), "destino": str(destino), "importe": importe})


def _ficha(hijos: list[dict], texto: str) -> dict:
    """La ficha del hijo, sólo si el id es de uno de ESTE tutor; si no, 404 sin tocar el TPV."""
    if _ID.fullmatch((texto or "").strip()):
        for h in hijos:
            if isinstance(h, dict) and h.get("id") == int(texto):
                return h
    raise HTTPException(404)


def _alcanza(origen: dict, importe: Decimal) -> bool:
    try:
        return Decimal(str(origen.get("disponible"))) >= importe
    except (InvalidOperation, ValueError, TypeError):  # valor raro del TPV: decide el TPV
        return True


def _contexto_confirmar(sesion, origen, destino, importe: Decimal, llave, aviso=None,
                        incierto=False) -> dict:
    de = f"De: {origen['nombre']} (disponible {dinero(origen.get('disponible'))})"
    nota = "Se mueve saldo disponible; no se puede deshacer desde el portal."
    ctx = {
        "titulo": "Confirma la transferencia",
        "titulo_fino": "Confirma la", "titulo_fuerte": "transferencia",
        "lineas": [de, f"Para: {destino['nombre']}", f"Importe: {dinero(importe)}", nota],
        "ticket": {"lineas": [de, f"Para: {destino['nombre']}"], "total": dinero(importe),
                   "total_etiqueta": "Importe"},
        "nota": nota,
        "aviso": aviso, "accion": "/transferir/confirmar", "csrf": sesion.csrf,
        "campos": {"origen_id": str(origen["id"]), "destino_id": str(destino["id"]),
                   "importe": f"{importe:.2f}", "llave": llave},
        "boton": "Confirmar transferencia",
        # «Cambiar» regresa con lo elegido; «Cancelar» al inicio.
        "volver": "/transferir?" + urlencode({"origen": origen["id"], "destino": destino["id"],
                                             "importe": f"{importe:.2f}"}),
        "cancelar": "/"}
    if incierto:
        # Sin respuesta del TPV la transferencia pudo aplicarse: ni «Cancelar» (no deshace nada)
        # ni «Cambiar» (otra llave = otra transferencia). Sólo reintentar con la MISMA llave.
        ctx.update(volver="/", volver_texto="Ver saldos", cancelar=None)
    return ctx


def _con_dos_o_mas(request, operador, tutor):
    """Los hijos, o la respuesta que corresponde si no se puede transferir."""
    try:
        hijos = tutor_tpv.hijos(request, operador, tutor)
    except SinTPV:
        return None, _error(request, MSG_SIN_TPV)
    hijos = [h for h in hijos if isinstance(h, dict) and isinstance(h.get("id"), int)]
    if len(hijos) < 2:
        return None, RedirectResponse("/", status_code=303)
    return hijos, None


@router.get("/transferir")
def formulario(request: Request, par: tuple[Sesion, Tutor] = Depends(tutor_listo),
               operador: Operador = Depends(operador_actual)):
    sesion, tutor = par
    hijos, otra = _con_dos_o_mas(request, operador, tutor)
    if otra:
        return otra
    # «Cambiar» desde la confirmación regresa con lo elegido (sólo para pintar: se valida al revisar).
    q = request.query_params
    return _formulario(request, sesion, hijos, None, q.get("origen", "")[:12],
                       q.get("destino", "")[:12], q.get("importe", "")[:20])


@router.post("/transferir/revisar")
def revisar(request: Request, csrf: str = Form(""), origen_id: str = Form(""),
            destino_id: str = Form(""), importe: str = Form(""),
            par: tuple[Sesion, Tutor] = Depends(tutor_listo),
            operador: Operador = Depends(operador_actual)):
    sesion, tutor = par
    exigir_csrf(csrf, sesion.csrf)
    hijos, otra = _con_dos_o_mas(request, operador, tutor)
    if otra:
        return otra

    def de_nuevo(mensaje):
        return _formulario(request, sesion, hijos, mensaje, origen_id, destino_id, importe)

    if not origen_id.strip() or not destino_id.strip():
        return de_nuevo(MSG_ELIGE)
    origen, destino = _ficha(hijos, origen_id), _ficha(hijos, destino_id)
    if origen["id"] == destino["id"]:
        return de_nuevo(MSG_DISTINTOS)
    monto = parse_importe(importe)
    if monto is None:
        return de_nuevo(MSG_IMPORTE)
    if not _alcanza(origen, monto):
        return de_nuevo(f"{origen['nombre']} no tiene tanto: su disponible es "
                        f"{dinero(origen.get('disponible'))}.")
    return _pagina(request, "tutor/confirmar.html", _contexto_confirmar(
        sesion, origen, destino, monto, uuid.uuid4().hex))


@router.post("/transferir/confirmar")
def confirmar(request: Request, csrf: str = Form(""), origen_id: str = Form(""),
              destino_id: str = Form(""), importe: str = Form(""), llave: str = Form(""),
              par: tuple[Sesion, Tutor] = Depends(tutor_listo),
              operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, tutor = par
    exigir_csrf(csrf, sesion.csrf)
    monto = parse_importe(importe)
    try:
        hijos = tutor_tpv.hijos(request, operador, tutor)
    except SinTPV:
        # Un intento anterior pudo quedar sin respuesta: se conserva el mismo envío (misma llave).
        if LLAVE.fullmatch(llave) and monto is not None and _ID.fullmatch(origen_id.strip()) \
                and _ID.fullmatch(destino_id.strip()):
            return _pagina(request, "tutor/confirmar.html", {
                "titulo": "Confirma la transferencia", "lineas": [f"Importe: {dinero(monto)}"],
                "aviso": MSG_SIN_TPV, "accion": "/transferir/confirmar", "csrf": sesion.csrf,
                "campos": {"origen_id": origen_id.strip(), "destino_id": destino_id.strip(),
                           "importe": f"{monto:.2f}", "llave": llave},
                "boton": "Reintentar", "volver": "/", "volver_texto": "Ver saldos"})
        return _error(request, MSG_SIN_TPV)
    origen, destino = _ficha(hijos, origen_id), _ficha(hijos, destino_id)
    if not LLAVE.fullmatch(llave):
        return _error(request, msg_llave(OP))
    if origen["id"] == destino["id"]:
        return _error(request, MSG_DISTINTOS)
    if monto is None:
        return _error(request, MSG_IMPORTE)
    # Con la llave ya registrada (200), el TPV repite sin re-aplicar y devuelve los saldos de hoy:
    # un doble toque ve el resultado aunque el origen ya no tenga ese disponible.
    # Igual con un intento anterior sin respuesta: pudo aplicarse y el saldo de ahora ya es el
    # descontado; reenviar con la MISMA llave es seguro (si no entró, el TPV rechaza por saldo).
    ya_entro = ya_registrado(db, operador, tutor, llave, OP) or intento_incierto(
        db, operador, tutor, llave, OP)
    pareja = {"nombre": f"{origen['nombre']} y {destino['nombre']}"}
    if not ya_entro and not _alcanza(origen, monto):
        return _error(request, con_aviso(
            db, operador, tutor, llave, pareja,
            f"{origen['nombre']} no tiene tanto: su disponible es {dinero(origen.get('disponible'))}.",
            OP, "volver a transferir"))
    cuerpo = {"origen_id": origen["id"], "destino_id": destino["id"],
              # Número JSON con 2 decimales: `float` de un Decimal de 2 decimales se serializa con
              # su repr más corto (150.5, 0.1), sin ruido binario; el TPV lo redondea a centavos.
              "importe": float(monto), "llave": llave}
    estado, r = enviar_escritura(db, request, operador, tutor, OP, cuerpo, llave, pareja,
                                 "volver a transferir")
    if estado == "incierto":
        return _pagina(request, "tutor/confirmar.html", _contexto_confirmar(
            sesion, origen, destino, monto, llave, MSG_SIN_RESPUESTA, incierto=True))
    if estado == "rechazo":
        # Si un intento anterior con esta llave quedó sin respuesta, este rechazo no dice si aquél
        # se aplicó: a ver saldos, no a armar otra transferencia con otra llave (Codex).
        if ya_entro:
            return _error(request, r, volver="/", titulo=TITULO_INCIERTO)
        return _error(request, r)
    # Sólo ids enteros: uno raro (objeto, lista) tumbaría la página justo después de transferir.
    nuevos = {a["id"]: a for a in r.get("alumnos") or []
              if isinstance(a, dict) and isinstance(a.get("id"), int)} \
        if isinstance(r.get("alumnos"), list) else {}
    n_origen, n_destino = nuevos.get(origen["id"]), nuevos.get(destino["id"])
    # Saldos que no son números (null, «NaN», texto) cuentan como faltantes (Codex).
    if n_origen is None or n_destino is None or _cantidad(n_origen.get("disponible")) is None \
            or _cantidad(n_destino.get("disponible")) is None:
        # El TPV dijo «ok» pero sin saldos: la transferencia pudo aplicarse. A ver saldos, no a
        # armar otra (otra llave = otra transferencia). Hallazgo de Codex.
        return _error(request, MSG_INESPERADO.format(pareja["nombre"]), volver="/",
                      titulo=TITULO_INCIERTO)
    return _pagina(request, "tutor/resultado.html", {
        "titulo": "Listo, saldo transferido",
        "titulo_fino": "Saldo", "titulo_fuerte": "transferido",
        "mensaje": f"Pasaste {dinero(monto)} de {origen['nombre']} a {destino['nombre']}.",
        "detalles": [f"Disponible de {origen['nombre']}: {dinero(n_origen['disponible'])}",
                     f"Disponible de {destino['nombre']}: {dinero(n_destino['disponible'])}"],
        "volver": "/", "error": False})
