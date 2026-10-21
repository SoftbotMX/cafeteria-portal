"""Lecturas (y escrituras) del tutor al TPV. El `tutor_ref` sale siempre de la sesión."""
import time
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from portal.alta import escribir_tpv
from portal.models import Bitacora, Operador, Tutor
from portal.tpv import ErrorTPV, cliente_tutor

MSG_SIN_TPV = "No pudimos consultar el saldo, intenta en un momento."
MSG_SIN_PEDIDOS = "No pudimos consultar los pedidos, intenta en un momento."
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


def pedidos(request: Request, operador: Operador, tutor: Tutor, alumno_id: int) -> list[dict]:
    """Los 50 pedidos más recientes del hijo, con `fecha_entrega` ya como `date`. Lo que venga
    mal formado se descarta: un pedido sin id, folio o día claros no se ofrece ni se cancela."""
    try:
        crudo = _cliente(request, operador).leer("alumno/pedidos", {
            "tutor_ref": tutor.tutor_ref, "alumno_id": alumno_id})["pedidos"]
        if not isinstance(crudo, list):
            raise TypeError
    except (ErrorTPV, KeyError, TypeError):
        raise SinTPV from None
    lista = []
    for p in crudo:
        if not isinstance(p, dict) or not isinstance(p.get("id"), int) or isinstance(p["id"], bool):
            continue
        try:
            entrega = date.fromisoformat(str(p["fecha_entrega"]))
        except (KeyError, ValueError):
            continue
        lineas = p.get("lineas") if isinstance(p.get("lineas"), list) else []
        lista.append({**p, "fecha_entrega": entrega, "nombre": str(p.get("nombre") or ""),
                      "lineas": [l for l in lineas if isinstance(l, dict)]})
    return lista


def es_incierto(e: ErrorTPV) -> bool:
    """¿No sabemos si la escritura entró? Sin respuesta, 5xx, cuerpo ilegible (`respuesta_invalida`,
    p. ej. una página HTML de Odoo por la llave única en un doble toque) o firma repetida. Sólo un
    4xx con un `codigo` JSON reconocible es un rechazo final."""
    return (e.status <= 0 or e.status >= 500
            or e.codigo in ("respuesta_invalida", "repetida") or not 400 <= e.status < 500)


MSG_CAMBIO = "Este pedido cambió desde que lo revisaste; vuelve a armarlo."
MSG_SIN_CONTACTO = "No se pudo contactar al TPV. Vuelve a intentar en un momento."


def mensaje_para_tutor(e: ErrorTPV, msg_cambio: str = MSG_CAMBIO) -> str:
    """Texto para un papá ante un rechazo del TPV (para las escrituras del tutor, Tasks 3–7).
    El `mensaje` del TPV ya es español claro, se muestra tal cual; nunca el texto de
    administración. Excepción: «ya se registró con otros datos» (llave con otro contenido) trae
    la llave interna; se reconoce por esa frase conocida del TPV y se cambia por `msg_cambio`,
    que cada operación redacta a su manera (MSG_CAMBIO es el de los pedidos)."""
    if e.codigo == "llave_reutilizada" or "ya se registró con otros datos" in e.mensaje:
        return msg_cambio
    if es_incierto(e):
        return MSG_SIN_CONTACTO
    return e.mensaje or "No se pudo completar la operación."


def escribir_tutor(db, request: Request, operador: Operador, tutor: Tutor, operacion: str,
                   cuerpo: dict) -> dict:
    """El cuerpo que llega no trae `tutor_ref`: se pone aquí, desde la sesión."""
    cuerpo = {**cuerpo, "tutor_ref": tutor.tutor_ref}
    return escribir_tpv(db, operador, _cliente(request, operador), f"tutor:{tutor.id}",
                        operacion, cuerpo)


def ya_registrado(db: Session, operador: Operador, tutor: Tutor, llave: str, operacion: str = "precompra") -> bool:
    return db.scalars(select(Bitacora.id).where(
        Bitacora.operador_id == operador.id, Bitacora.actor == f"tutor:{tutor.id}",
        Bitacora.operacion == operacion, Bitacora.llave == llave,
        Bitacora.http_status == 200, Bitacora.codigo_error.is_(None))).first() is not None


def intento_incierto(db: Session, operador: Operador, tutor: Tutor, llave: str,
                     operacion: str = "precompra") -> bool:
    """¿Hubo ya un envío con esta llave que no obtuvo respuesta clara del TPV?"""
    return db.scalars(select(Bitacora.id).where(
        Bitacora.operador_id == operador.id, Bitacora.actor == f"tutor:{tutor.id}",
        Bitacora.operacion == operacion, Bitacora.llave == llave,
        or_(Bitacora.http_status < 200, Bitacora.http_status >= 500,
            # misma regla que `es_incierto`: 4xx sin JSON reconocible, o 200 ilegible
            Bitacora.codigo_error.in_(("respuesta_invalida", "repetida"))))).first() is not None


def con_aviso(db, operador, tutor, llave, hijo, mensaje: str,
              operacion: str = "precompra", reintento: str = "volver a pedir",
              que: str = "los movimientos") -> str:
    """Si un envío anterior con esta llave quedó sin respuesta, un rechazo de ahora no prueba que
    aquél fallara: puede haberse registrado. Se le dice al tutor que lo revise."""
    if intento_incierto(db, operador, tutor, llave, operacion):
        mensaje += (f" Ojo: tu intento anterior no tuvo respuesta y puede haberse registrado. "
                    f"Revisa {que} de {hijo['nombre']} antes de {reintento}.")
    return mensaje


def enviar_escritura(db, request: Request, operador: Operador, tutor: Tutor, operacion: str,
                     cuerpo: dict, llave: str, hijo: dict, reintento: str, msg_cambio: str,
                     que: str = "los movimientos") -> tuple[str, object]:
    """Manda la escritura y deja la bitácora confirmada (un `commit`, también si falló).
    ("ok", respuesta) | ("incierto", None): no sabemos si entró, se reintenta con la MISMA llave
    | ("rechazo", mensaje para el tutor)."""
    try:
        r = escribir_tutor(db, request, operador, tutor, operacion, cuerpo)
    except ErrorTPV as e:
        db.commit()  # la bitácora del intento fallido es verdad
        if es_incierto(e):
            return "incierto", None
        return "rechazo", con_aviso(db, operador, tutor, llave, hijo, mensaje_para_tutor(e, msg_cambio),
                                    operacion, reintento, que)
    db.commit()
    return "ok", r
