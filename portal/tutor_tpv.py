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
        r = _cliente(request, operador).llamar("tutor/alumnos", {"tutor_ref": tutor.tutor_ref})
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
    cache = request.app.state.__dict__.setdefault("cache_menu", {})
    if cacheado:
        hit = cache.get(operador.id)
        if hit and time.monotonic() - hit[0] < CACHE_MENU_SEGUNDOS:
            return hit[1]
    productos = _leer_menu(request, operador)
    cache[operador.id] = (time.monotonic(), productos)
    return productos


def _leer_menu(request: Request, operador: Operador) -> list[dict]:
    try:
        crudo = _cliente(request, operador).llamar("menu", {})["productos"]
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
        r = _cliente(request, operador).llamar("alumno/movimientos", {
            "tutor_ref": tutor.tutor_ref, "alumno_id": alumno_id,
            "pagina": pagina, "por_pagina": por_pagina})
        return {"total": int(r["total"]), "movimientos": list(r["movimientos"])}
    except (ErrorTPV, KeyError, TypeError, ValueError):
        raise SinTPV from None


def escribir_tutor(db, request: Request, operador: Operador, tutor: Tutor, operacion: str,
                   cuerpo: dict) -> dict:
    """El cuerpo que llega no trae `tutor_ref`: se pone aquí, desde la sesión."""
    cuerpo = {**cuerpo, "tutor_ref": tutor.tutor_ref}
    return escribir_tpv(db, operador, _cliente(request, operador), f"tutor:{tutor.id}",
                        operacion, cuerpo)
