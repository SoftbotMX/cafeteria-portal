from fastapi import APIRouter, Depends, Request

from portal.models import Sesion, Tutor
from portal.sesiones import tutor_listo
from portal.web import templates

router = APIRouter()


@router.get("/")
def inicio(request: Request, par: tuple[Sesion, Tutor] = Depends(tutor_listo)):
    sesion, tutor = par
    return templates.TemplateResponse(
        request, "tutor_inicio.html", {"tutor": tutor, "csrf": sesion.csrf})
