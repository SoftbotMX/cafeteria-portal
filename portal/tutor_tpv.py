"""Lecturas (y escrituras) del tutor al TPV. El `tutor_ref` sale siempre de la sesión."""
from fastapi import HTTPException, Request

from portal.alta import escribir_tpv
from portal.models import Operador, Tutor
from portal.tpv import ErrorTPV, cliente_tutor

MSG_SIN_TPV = "No pudimos consultar el saldo, intenta en un momento."


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
