"""Páginas públicas que pide Meta para publicar la app de WhatsApp: aviso de privacidad,
condiciones del servicio e instrucciones para eliminar datos. Sin sesión ni base de datos."""
from fastapi import APIRouter, Request

from portal.web import templates

router = APIRouter()

RESPONSABLE = {
    "nombre": "Softbot",
    "domicilio": "Zaragoza 102, Zona Centro, Ciudad Victoria, Tamaulipas, C.P. 87000, México",
    "correo": "contacto@softbot.com.mx",
    "actualizado": "8 de noviembre de 2026",
}


def _pagina(request: Request, plantilla: str):
    resp = templates.TemplateResponse(request, f"legal/{plantilla}.html", {"r": RESPONSABLE})
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@router.get("/privacidad")
def privacidad(request: Request):
    return _pagina(request, "privacidad")


@router.get("/terminos")
def terminos(request: Request):
    return _pagina(request, "terminos")


@router.get("/eliminar-datos")
def eliminar_datos(request: Request):
    return _pagina(request, "eliminar_datos")
