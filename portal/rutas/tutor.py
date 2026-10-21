from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from portal import tutor_tpv
from portal.models import Operador, Sesion, Tutor
from portal.sesiones import operador_actual, tutor_listo
from portal.tutor_tpv import MSG_SIN_TPV, SinTPV
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


@router.get("/hijos/{alumno_id}")
def hijo(alumno_id: int, request: Request, par: tuple[Sesion, Tutor] = Depends(tutor_listo),
         operador: Operador = Depends(operador_actual)):
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
