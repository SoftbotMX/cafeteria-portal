from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from sqlalchemy.orm import Session

from portal import tutor_tpv
from portal.db import get_db
from portal.models import Operador, Sesion, Tutor
from portal.reglas import ahora_local, ahora_utc, cargar_reglas, puede_cancelar
from portal.sesiones import operador_actual, tutor_listo
from portal.tutor_tpv import MSG_SIN_PEDIDOS, MSG_SIN_TPV, SinTPV
from portal.web import templates

router = APIRouter()

POR_PAGINA = 20


@router.get("/")
def inicio(request: Request, par: tuple[Sesion, Tutor] = Depends(tutor_listo),
           operador: Operador = Depends(operador_actual)):
    sesion, tutor = par
    ctx = {"tutor": tutor, "csrf": sesion.csrf, "hijos": [], "error_tpv": None}
    try:
        hijos = tutor_tpv.hijos(request, operador, tutor)
    except SinTPV:
        ctx["error_tpv"] = MSG_SIN_TPV
    else:
        if len(hijos) == 1 and isinstance(hijos[0].get("id"), int):
            return RedirectResponse(f"/hijos/{hijos[0]['id']}", status_code=303)
        ctx["hijos"] = hijos
    return templates.TemplateResponse(request, "tutor_inicio.html", ctx)


def _contexto_movs(request, operador, tutor, alumno_id: int, pagina: int) -> dict:
    datos = tutor_tpv.movimientos(request, operador, tutor, alumno_id, pagina, POR_PAGINA)
    return {"movs": datos["movimientos"], "alumno_id": alumno_id, "pagina": pagina,
            "hay_mas": pagina * POR_PAGINA < datos["total"], "error_tpv": None}


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
def hijo(alumno_id: int, request: Request, par: tuple[Sesion, Tutor] = Depends(tutor_listo),
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
           "error_tpv": None}
    try:
        ctx.update(_contexto_pedidos(request, operador, tutor, db, alumno_id, ahora))
    except SinTPV:
        ctx.update(pedidos=[], error_pedidos=MSG_SIN_PEDIDOS)
    try:
        ctx.update(_contexto_movs(request, operador, tutor, alumno_id, 1))
    except SinTPV:
        ctx.update(movs=[], hay_mas=False, error_mov=MSG_SIN_TPV, alumno_id=alumno_id, pagina=1)
    return templates.TemplateResponse(request, "tutor/hijo.html", ctx)


@router.get("/hijos/{alumno_id}/movimientos")
def movimientos(alumno_id: int, request: Request, pagina: int = Query(1, ge=1, le=10000),
                par: tuple[Sesion, Tutor] = Depends(tutor_listo),
                operador: Operador = Depends(operador_actual)):
    _, tutor = par
    try:
        tutor_tpv.hijo_o_404(request, operador, tutor, alumno_id)
        ctx = _contexto_movs(request, operador, tutor, alumno_id, pagina)
    except SinTPV:
        ctx = {"movs": [], "hay_mas": False, "alumno_id": alumno_id, "pagina": pagina,
               "error_mov": MSG_SIN_TPV}
    return templates.TemplateResponse(request, "tutor/_movimientos.html", ctx)
