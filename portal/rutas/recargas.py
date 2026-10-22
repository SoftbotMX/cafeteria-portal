"""Recargar saldo con MercadoPago (Checkout Pro). El núcleo vive en `portal.recargas`."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from portal import recargas, tutor_tpv
from portal.db import get_db
from portal.formato import dinero
from portal.mp import ErrorMP
from portal.models import Operador, Recarga, Sesion, Tutor
from portal.sesiones import exigir_csrf, operador_actual, tutor_listo
from portal.tutor_tpv import LLAVE, MSG_SIN_TPV, SIN_CACHE, SinTPV
from portal.web import templates

router = APIRouter()

# Las rutas que leen el formulario son `async` (los importes llegan como `importe_<id>`), pero
# todo lo que espera red o BD corre en el pool de hilos: en el hilo del servidor congelaría el
# portal entero mientras MP o el TPV responden (Codex P1).

MSG_SIN_MP = ("No pudimos abrir Mercado Pago. No se te cobró nada: vuelve a tocar «Pagar con "
              "Mercado Pago» en un momento.")
ESTADOS = {
    "pendiente": "Sin pagar", "aprobada": "Pagada, acreditando", "acreditada": "Acreditada",
    "rechazada": "Rechazada", "vencida": "No se pagó", "revision": "En revisión",
}


def _pagina(request, plantilla, ctx, status=200):
    return templates.TemplateResponse(request, plantilla, ctx, status_code=status, headers=SIN_CACHE)


def _con_mp(operador: Operador) -> None:
    if not operador.mp_access_token_cif:
        raise HTTPException(404)


def _hijos(request, operador, tutor):
    hijos = tutor_tpv.hijos(request, operador, tutor)
    return [h for h in hijos if isinstance(h, dict) and isinstance(h.get("id"), int)]


def _error(request, mensaje):
    return _pagina(request, "tutor/resultado.html", {
        "titulo": "No se pudo recargar", "mensaje": mensaje, "volver": "/recargar",
        "detalles": [], "error": True})


def _formulario(request, sesion, hijos, mensaje=None, valores=None):
    return _pagina(request, "tutor/recargar.html", {
        "hijos": hijos, "csrf": sesion.csrf, "error": mensaje, "valores": valores or {},
        "minimo": recargas.MINIMO, "maximo": recargas.MAXIMO})


def _confirmar(request, sesion, reparto, total, llave, aviso=None):
    campos = {f"importe_{p['alumno_id']}": str(p["importe"]) for p in reparto}
    campos["llave"] = llave
    return _pagina(request, "tutor/confirmar.html", {
        "titulo": "Confirma la recarga",
        "lineas": [f"{p['nombre']}: {dinero(p['importe'])}" for p in reparto]
        + [f"Total: {dinero(total)}",
           "Pagas en Mercado Pago con tarjeta o con tu saldo de Mercado Pago."],
        "aviso": aviso, "accion": "/recargar/confirmar", "csrf": sesion.csrf, "campos": campos,
        "boton": "Pagar con Mercado Pago", "volver": "/recargar"})


@router.get("/recargar")
def formulario(request: Request, par: tuple[Sesion, Tutor] = Depends(tutor_listo),
               operador: Operador = Depends(operador_actual)):
    _con_mp(operador)
    sesion, tutor = par
    try:
        hijos = _hijos(request, operador, tutor)
    except SinTPV:
        return _error(request, MSG_SIN_TPV)
    return _formulario(request, sesion, hijos)


@router.post("/recargar/revisar")
async def revisar(request: Request, par: tuple[Sesion, Tutor] = Depends(tutor_listo),
                  operador: Operador = Depends(operador_actual)):
    _con_mp(operador)
    sesion, tutor = par
    form = await request.form()
    exigir_csrf(form.get("csrf"), sesion.csrf)
    datos = {k: v for k, v in form.items() if isinstance(v, str)}
    return await run_in_threadpool(_revisar, request, sesion, tutor, operador, datos)


def _revisar(request, sesion, tutor, operador, datos):
    try:
        hijos = _hijos(request, operador, tutor)
    except SinTPV:
        return _error(request, MSG_SIN_TPV)
    armado = recargas.armar_reparto(hijos, datos)
    if isinstance(armado, str):
        return _formulario(request, sesion, hijos, armado, datos)
    reparto, total = armado
    return _confirmar(request, sesion, reparto, total, uuid.uuid4().hex)


@router.post("/recargar/confirmar")
async def confirmar(request: Request, par: tuple[Sesion, Tutor] = Depends(tutor_listo),
                    operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    _con_mp(operador)
    sesion, tutor = par
    form = await request.form()
    exigir_csrf(form.get("csrf"), sesion.csrf)
    datos = {k: v for k, v in form.items() if isinstance(v, str)}
    return await run_in_threadpool(_confirmar_pago, request, sesion, tutor, operador, db, datos)


def _confirmar_pago(request, sesion, tutor, operador, db, datos):
    llave = datos.get("llave", "")
    if not LLAVE.fullmatch(llave):
        return _error(request, "Esta recarga ya no es válida. Vuelve a armarla.")
    # Una recarga ya creada con esta llave se reusa tal cual (doble toque, reintento).
    previa = db.scalar(select(Recarga).where(Recarga.operador_id == operador.id,
                                             Recarga.tutor_id == tutor.id, Recarga.llave == llave))
    if previa is not None:
        reparto, total = previa.reparto, previa.total
    else:
        try:
            hijos = _hijos(request, operador, tutor)
        except SinTPV:
            return _error(request, MSG_SIN_TPV)
        armado = recargas.armar_reparto(hijos, datos)
        if isinstance(armado, str):
            return _error(request, armado)
        reparto, total = armado
    base = str(request.base_url).rstrip("/")
    try:
        r = recargas.crear(db, request.app, operador, tutor, reparto, total, llave, base)
    except ErrorMP:
        return _confirmar(request, sesion, reparto, total, llave, MSG_SIN_MP)
    return RedirectResponse(r.init_point, status_code=303)


def _propia(db: Session, operador: Operador, tutor: Tutor, recarga_id: int) -> Recarga:
    r = db.get(Recarga, recarga_id)
    if r is None or r.operador_id != operador.id or r.tutor_id != tutor.id:
        raise HTTPException(404)
    return r


@router.get("/recargas")
def lista(request: Request, par: tuple[Sesion, Tutor] = Depends(tutor_listo),
          operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    _, tutor = par
    filas = db.scalars(select(Recarga).where(Recarga.operador_id == operador.id,
                                             Recarga.tutor_id == tutor.id)
                       .order_by(Recarga.id.desc()).limit(50)).all()
    return _pagina(request, "tutor/recargas.html", {"recargas": filas, "estados": ESTADOS})


@router.get("/recargas/{recarga_id}")
def detalle(recarga_id: int, request: Request, par: tuple[Sesion, Tutor] = Depends(tutor_listo),
            operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    _, tutor = par
    r = _propia(db, operador, tutor, recarga_id)
    if r.estado in ("pendiente", "aprobada"):
        try:
            recargas.reintentar(db, request.app, operador, r.id)
            db.commit()
        except ErrorMP:
            db.rollback()  # MP no respondió: el aviso o el barrido lo terminan
        r = _propia(db, operador, tutor, recarga_id)
    vigente = r.estado == "pendiente" and r.init_point and r.liga_vence and \
        datetime.now(timezone.utc) < r.liga_vence
    return _pagina(request, "tutor/recarga.html", {"r": r, "vigente": vigente})


def _id_de_pago(request: Request, cuerpo) -> str | None:
    """El id del pago de un aviso de MP (webhook: `data.id` + `type`; IPN: `id` + `topic`).
    Sólo el id: lo demás del aviso no se usa para nada."""
    q = request.query_params
    tipo = q.get("type") or q.get("topic")
    pid = q.get("data.id") or q.get("id")
    if isinstance(cuerpo, dict):
        tipo = tipo or cuerpo.get("type") or cuerpo.get("topic")
        datos = cuerpo.get("data")
        if not pid and isinstance(datos, dict):
            pid = datos.get("id")
    pid = str(pid or "").strip()
    if tipo != "payment" or not pid.isdigit() or len(pid) > 20:
        return None
    return pid


@router.post("/mp/aviso")
async def aviso_mp(request: Request, operador: Operador = Depends(operador_actual),
                   db: Session = Depends(get_db)):
    """Público y sin CSRF: lo llama MercadoPago. No se le cree: se consulta el pago en su API.
    200 = procesado o ignorado (MP no reintenta); 503 = MP no respondió (que reintente)."""
    try:
        cuerpo = await request.json()
    except ValueError:
        cuerpo = None
    pid = _id_de_pago(request, cuerpo)
    if pid is None:
        return {"ok": True}
    return await run_in_threadpool(_procesar_aviso, request, operador, db, pid)


def _procesar_aviso(request, operador, db, pid):
    try:
        recargas.procesar_pago(db, request.app, operador, pid)
        db.commit()
    except ErrorMP:
        db.rollback()
        return JSONResponse({"ok": False}, status_code=503)
    return {"ok": True}
