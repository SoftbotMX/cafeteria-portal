"""Transferir saldo disponible de un hijo a otro (operación `transferencia` del TPV).

El portal no guarda saldos: el disponible de cada hijo se lee del TPV en cada paso, y los
saldos nuevos que se muestran son los de la respuesta del TPV, nunca una resta local.
"""
import re
import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from portal import tutor_tpv
from portal.db import get_db
from portal.formato import dinero, parse_importe
from portal.models import Operador, Sesion, Tutor
from portal.sesiones import exigir_csrf, operador_actual, tutor_listo
from portal.tutor_tpv import MSG_SIN_TPV, SinTPV, enviar_escritura, intento_incierto, ya_registrado
from portal.web import templates

router = APIRouter()

OP = "transferencia"
SIN_CACHE = {"Cache-Control": "no-store"}
_ID = re.compile(r"[0-9]{1,12}")
_LLAVE = re.compile(r"[A-Za-z0-9_-]{8,100}")
MSG_LLAVE = "La confirmación no es válida. Vuelve a revisar tu transferencia."
MSG_IMPORTE = "Escribe un importe válido, mayor que cero y con máximo 2 decimales (por ejemplo 150.50)."
MSG_DISTINTOS = "Elige dos hijos distintos: de quién sale el saldo y a quién llega."
MSG_ELIGE = "Elige de quién sale el saldo y a quién llega."
MSG_CAMBIO = "Esta transferencia cambió desde que la revisaste; vuelve a hacerla."
MSG_SIN_RESPUESTA = ("No se pudo contactar al TPV. Tu transferencia puede haberse registrado: vuelve a "
                     "tocar «Confirmar transferencia» (no se duplica) o revisa los movimientos de tus hijos.")
MSG_INESPERADO = ("El TPV respondió algo inesperado. Revisa los movimientos de {} antes de volver a "
                  "transferir.")
TITULO_ERROR = "No se pudo transferir el saldo"


def _pagina(request, plantilla, ctx, status=200):
    return templates.TemplateResponse(request, plantilla, ctx, status_code=status, headers=SIN_CACHE)


def _error(request, mensaje: str):
    return _pagina(request, "tutor/resultado.html", {
        "titulo": TITULO_ERROR, "mensaje": mensaje, "volver": "/transferir", "detalles": [],
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


def _contexto_confirmar(sesion, origen, destino, importe: Decimal, llave, aviso=None) -> dict:
    return {
        "titulo": "Confirma la transferencia",
        "lineas": [f"De: {origen['nombre']} (disponible {dinero(origen.get('disponible'))})",
                   f"Para: {destino['nombre']}", f"Importe: {dinero(importe)}",
                   "Se mueve saldo disponible; no se puede deshacer desde el portal."],
        "aviso": aviso, "accion": "/transferir/confirmar", "csrf": sesion.csrf,
        "campos": {"origen_id": str(origen["id"]), "destino_id": str(destino["id"]),
                   "importe": f"{importe:.2f}", "llave": llave},
        "boton": "Confirmar transferencia", "volver": "/transferir"}


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
    return otra or _formulario(request, sesion, hijos)


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
        if _LLAVE.fullmatch(llave) and monto is not None and _ID.fullmatch(origen_id.strip()) \
                and _ID.fullmatch(destino_id.strip()):
            return _pagina(request, "tutor/confirmar.html", {
                "titulo": "Confirma la transferencia", "lineas": [f"Importe: {dinero(monto)}"],
                "aviso": MSG_SIN_TPV, "accion": "/transferir/confirmar", "csrf": sesion.csrf,
                "campos": {"origen_id": origen_id.strip(), "destino_id": destino_id.strip(),
                           "importe": f"{monto:.2f}", "llave": llave},
                "boton": "Reintentar", "volver": "/transferir"})
        return _error(request, MSG_SIN_TPV)
    origen, destino = _ficha(hijos, origen_id), _ficha(hijos, destino_id)
    if not _LLAVE.fullmatch(llave):
        return _error(request, MSG_LLAVE)
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
        return _error(request, tutor_tpv.con_aviso(
            db, operador, tutor, llave, pareja,
            f"{origen['nombre']} no tiene tanto: su disponible es {dinero(origen.get('disponible'))}.",
            OP, "volver a transferir"))
    cuerpo = {"origen_id": origen["id"], "destino_id": destino["id"],
              # Número JSON con 2 decimales: `float` de un Decimal de 2 decimales se serializa con
              # su repr más corto (150.5, 0.1), sin ruido binario; el TPV lo redondea a centavos.
              "importe": float(monto), "llave": llave}
    estado, r = enviar_escritura(db, request, operador, tutor, OP, cuerpo, llave, pareja,
                                 "volver a transferir", MSG_CAMBIO)
    if estado == "incierto":
        return _pagina(request, "tutor/confirmar.html", _contexto_confirmar(
            sesion, origen, destino, monto, llave, MSG_SIN_RESPUESTA))
    if estado == "rechazo":
        return _error(request, r)
    nuevos = {a.get("id"): a for a in r.get("alumnos") or [] if isinstance(a, dict)} \
        if isinstance(r.get("alumnos"), list) else {}
    n_origen, n_destino = nuevos.get(origen["id"]), nuevos.get(destino["id"])
    if n_origen is None or n_destino is None or "disponible" not in n_origen \
            or "disponible" not in n_destino:
        return _error(request, MSG_INESPERADO.format(pareja["nombre"]))
    return _pagina(request, "tutor/resultado.html", {
        "titulo": "Listo, saldo transferido",
        "mensaje": f"Pasaste {dinero(monto)} de {origen['nombre']} a {destino['nombre']}.",
        "detalles": [f"Disponible de {origen['nombre']}: {dinero(n_origen['disponible'])}",
                     f"Disponible de {destino['nombre']}: {dinero(n_destino['disponible'])}"],
        "volver": "/", "error": False})
