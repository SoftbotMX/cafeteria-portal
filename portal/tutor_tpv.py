"""Lecturas (y escrituras) del tutor al TPV. El `tutor_ref` sale siempre de la sesión."""
import time
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException, Request

from portal.alta import escribir_tpv
from portal.models import Operador, Tutor
from portal.tpv import ErrorTPV, cliente_tutor

MSG_SIN_TPV = "No pudimos consultar el saldo, intenta en un momento."
MSG_SIN_MENU = "No pudimos consultar el menú, intenta en un momento."


class SinTPV(Exception):
    """El TPV no respondió o respondió algo inesperado al leer."""


def _cliente(request: Request, operador: Operador):
    return cliente_tutor(request.app, operador, request.app.state.settings.fernet_key)


def hijos(request: Request, operador: Operador, tutor: Tutor) -> list[dict]:
    try:
        r = _cliente(request, operador).leer("tutor/alumnos", {"tutor_ref": tutor.tutor_ref})
        fichas = r["alumnos"]
        if not isinstance(fichas, list):
            raise TypeError
        return fichas
    except (ErrorTPV, KeyError, TypeError):
        raise SinTPV from None


def hijo_o_404(request: Request, operador: Operador, tutor: Tutor,
               alumno_id: int) -> tuple[dict, list[dict]]:
    """Un alumno de la URL sólo vale si está entre los hijos de este tutor."""
    todos = hijos(request, operador, tutor)
    for h in todos:
        if isinstance(h, dict) and h.get("id") == alumno_id:
            return h, todos
    raise HTTPException(404)


CACHE_MENU_SEGUNDOS = 30


def menu(request: Request, operador: Operador, cacheado: bool = False) -> list[dict]:
    """Productos con `id` (int), `nombre`, `categoria` y `precio` (Decimal). Lo que el TPV
    mande mal formado se descarta: un producto sin precio claro no se ofrece.

    `cacheado`: sólo para PINTAR (página de pedir, total en vivo): cada +/− sería otra llamada
    idéntica y firmada al TPV. Revisar y confirmar leen siempre el menú fresco."""
    estado = request.app.state  # `cache_menu` y su candado nacen en create_app
    if cacheado:
        with estado.cache_menu_candado:
            hit = estado.cache_menu.get(operador.id)
        if hit and time.monotonic() - hit[0] < CACHE_MENU_SEGUNDOS:
            return hit[1]
    productos = _leer_menu(request, operador)
    with estado.cache_menu_candado:
        estado.cache_menu[operador.id] = (time.monotonic(), productos)
    return productos


def _leer_menu(request: Request, operador: Operador) -> list[dict]:
    try:
        crudo = _cliente(request, operador).leer("menu", {})["productos"]
        if not isinstance(crudo, list):
            raise TypeError
    except (ErrorTPV, KeyError, TypeError):
        raise SinTPV from None
    productos = []
    for p in crudo:
        if not isinstance(p, dict) or not isinstance(p.get("id"), int) or isinstance(p["id"], bool):
            continue
        try:
            precio = Decimal(str(p["precio"]))
        except (InvalidOperation, KeyError, ValueError):
            continue
        if not precio.is_finite() or precio < 0:
            continue
        productos.append({"id": p["id"], "nombre": str(p.get("nombre") or ""),
                          "categoria": str(p.get("categoria") or ""), "precio": precio})
    return productos


def movimientos(request: Request, operador: Operador, tutor: Tutor, alumno_id: int,
                pagina: int, por_pagina: int) -> dict:
    try:
        r = _cliente(request, operador).leer("alumno/movimientos", {
            "tutor_ref": tutor.tutor_ref, "alumno_id": alumno_id,
            "pagina": pagina, "por_pagina": por_pagina})
        return {"total": int(r["total"]), "movimientos": list(r["movimientos"])}
    except (ErrorTPV, KeyError, TypeError, ValueError):
        raise SinTPV from None


def es_incierto(e: ErrorTPV) -> bool:
    """¿No sabemos si la escritura entró? Sin respuesta, 5xx, cuerpo ilegible (`respuesta_invalida`,
    p. ej. una página HTML de Odoo por la llave única en un doble toque) o firma repetida. Sólo un
    4xx con un `codigo` JSON reconocible es un rechazo final."""
    return (e.status <= 0 or e.status >= 500
            or e.codigo in ("respuesta_invalida", "repetida") or not 400 <= e.status < 500)


MSG_CAMBIO = "Este pedido cambió desde que lo revisaste; vuelve a armarlo."
MSG_SIN_CONTACTO = "No se pudo contactar al TPV. Vuelve a intentar en un momento."


def mensaje_para_tutor(e: ErrorTPV) -> str:
    """Texto para un papá ante un rechazo del TPV (para las escrituras del tutor, Tasks 3–7).
    El `mensaje` del TPV ya es español claro, se muestra tal cual; nunca el texto de
    administración. Excepción: «ya se registró con otros datos» (llave con otro contenido) trae
    la llave interna; se reconoce por esa frase conocida del TPV y se cambia por MSG_CAMBIO."""
    if e.codigo == "llave_reutilizada" or "ya se registró con otros datos" in e.mensaje:
        return MSG_CAMBIO
    if es_incierto(e):
        return MSG_SIN_CONTACTO
    return e.mensaje or "No se pudo completar la operación."


def escribir_tutor(db, request: Request, operador: Operador, tutor: Tutor, operacion: str,
                   cuerpo: dict) -> dict:
    """El cuerpo que llega no trae `tutor_ref`: se pone aquí, desde la sesión."""
    cuerpo = {**cuerpo, "tutor_ref": tutor.tutor_ref}
    return escribir_tpv(db, operador, _cliente(request, operador), f"tutor:{tutor.id}",
                        operacion, cuerpo)
