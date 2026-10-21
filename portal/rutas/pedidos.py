"""Pedir con fecha: elegir día, armar el carrito, revisar y confirmar (precompra del TPV).

Lo que se muestra en dinero sale de los precios del menú sólo para orientar; el TPV pone los
precios reales y la página de resultado muestra SU total.
"""
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from portal import tutor_tpv
from portal.alta import mensaje_tpv
from portal.db import get_db
from portal.formato import ESTADOS_PEDIDO, dinero, fecha as fmt_fecha
from portal.models import Bitacora, Operador, Sesion, Tutor
from portal.reglas import ahora_fresca, ahora_utc, cargar_reglas, fechas_disponibles, motivo_fecha_invalida
from portal.sesiones import exigir_csrf, operador_actual, tutor_listo
from portal.tpv import ErrorTPV
from portal.tutor_tpv import MSG_SIN_MENU, MSG_SIN_TPV, SinTPV, escribir_tutor
from portal.web import templates

router = APIRouter()

# Tope por producto: un pedido de comedor escolar no pasa de unas cuantas piezas por línea, y un
# tope corto frena el error de dedo (200 en vez de 2).
MAX_POR_LINEA = 20
MSG_CANTIDAD = f"Cantidad no válida: usa números enteros del 1 al {MAX_POR_LINEA} por producto."
MSG_VACIO = "Elige al menos un producto."
MSG_FECHA = "Fecha no válida."
MSG_LLAVE = "La confirmación no es válida. Vuelve a revisar tu pedido."
MSG_SIN_RESPUESTA = ("No se pudo contactar al TPV. Tu pedido puede haberse registrado: vuelve a "
                     "tocar «Confirmar pedido» (no se duplica) o revisa los movimientos de tu hijo.")
SIN_CACHE = {"Cache-Control": "no-store"}
_ENTERO = re.compile(r"[0-9]{1,9}")
_LLAVE = re.compile(r"[A-Za-z0-9_-]{8,100}")


async def _formulario(request: Request):
    """Los campos `cant_{id}` son dinámicos: se leen del formulario completo."""
    return await request.form()


# ---------------------------------------------------------------- utilidades

def _fecha(texto: str | None) -> date | None:
    try:
        return date.fromisoformat((texto or "").strip())
    except ValueError:
        return None


def _entero(texto: str | None, minimo: int) -> int | None:
    """Entero decimal ASCII entre `minimo` y el tope por línea; None si no lo es."""
    t = (texto or "").strip()
    if not _ENTERO.fullmatch(t):
        return None
    n = int(t)
    return n if minimo <= n <= MAX_POR_LINEA else None


def _agrupar(menu: list[dict]) -> list[tuple[str, list[dict]]]:
    grupos: dict[str, list[dict]] = {}
    for p in menu:
        grupos.setdefault(p["categoria"] or "Otros", []).append(p)
    return list(grupos.items())


def _total(menu: list[dict], cantidades: dict[int, int]) -> Decimal:
    return sum((p["precio"] * cantidades.get(p["id"], 0) for p in menu), Decimal(0))


def _pagina(request, plantilla, ctx, status=200):
    return templates.TemplateResponse(request, plantilla, ctx, status_code=status, headers=SIN_CACHE)


def _error(request, mensaje: str, volver: str):
    return _pagina(request, "tutor/resultado.html", {
        "titulo": "No se pudo hacer el pedido", "mensaje": mensaje, "volver": volver,
        "detalles": [], "error": True})


def validar_lineas(pares: list[tuple[str, str]], menu: list[dict],
                   cero_omite: bool) -> tuple[list[tuple[int, int]], str | None]:
    """(producto_id, cantidad) ya válidos, o el mensaje de error. Todo se comprueba aquí, antes
    de hablar con el TPV. `cero_omite`: en el formulario un 0 es «no lo quiero»; en la
    confirmación no existe (la revisión sólo guarda lo pedido)."""
    ids_menu = {p["id"] for p in menu}
    items: list[tuple[int, int]] = []
    vistos: set[int] = set()
    for pid_txt, cant_txt in pares:
        if cero_omite and (cant_txt or "").strip() in ("", "0"):
            continue
        cantidad = _entero(cant_txt, 1)
        if cantidad is None:
            return [], MSG_CANTIDAD
        if not _ENTERO.fullmatch((pid_txt or "").strip()):
            return [], "Hay un producto que no está en el menú."
        pid = int(pid_txt.strip())
        if pid not in ids_menu:
            return [], "Hay un producto que no está en el menú."
        if pid in vistos:
            return [], "Un producto aparece repetido en el pedido."
        vistos.add(pid)
        items.append((pid, cantidad))
    if not items:
        return [], MSG_VACIO
    return items, None


def _cantidades_tolerantes(form) -> dict[int, int]:
    """Para pintar la página: lo que no sea una cantidad válida cuenta como 0."""
    cant: dict[int, int] = {}
    for k in form.keys():
        if k.startswith("cant_") and _ENTERO.fullmatch(k[5:]):
            n = _entero(form.get(k), 0)
            cant[int(k[5:])] = n or 0
    return cant


def _hijo(request, operador, tutor, alumno_id):
    return tutor_tpv.hijo_o_404(request, operador, tutor, alumno_id)[0]


def _pagina_pedir(request, sesion, tutor, operador, db, hijo, ahora, fecha_txt, cantidades=None,
                  error=None):
    reglas = cargar_reglas(db, operador.id)
    ctx = {"tutor": tutor, "csrf": sesion.csrf, "hijo": hijo, "alumno_id": hijo["id"],
           "fechas": fechas_disponibles(reglas, ahora), "fecha": None, "grupos": [],
           "cantidades": cantidades or {}, "total": Decimal(0), "error": error,
           "error_tpv": None}
    if ctx["fechas"]:
        pedida = _fecha(fecha_txt)
        if fecha_txt and pedida is None:
            ctx["error"] = ctx["error"] or MSG_FECHA
        elif pedida is not None:
            motivo = motivo_fecha_invalida(reglas, pedida, ahora)
            if motivo:
                ctx["error"] = ctx["error"] or motivo
            else:
                ctx["fecha"] = pedida
        ctx["fecha"] = ctx["fecha"] or ctx["fechas"][0]
        try:
            menu = tutor_tpv.menu(request, operador, cacheado=True)
        except SinTPV:
            ctx["error_tpv"] = MSG_SIN_MENU
        else:
            ctx["grupos"] = _agrupar(menu)
            ctx["total"] = _total(menu, ctx["cantidades"])
    return templates.TemplateResponse(request, "tutor/pedir.html", ctx)


# --------------------------------------------------------------------- rutas

@router.get("/hijos/{alumno_id}/pedir")
def pedir(alumno_id: int, request: Request, fecha: str | None = Query(None, max_length=40),
          par: tuple[Sesion, Tutor] = Depends(tutor_listo),
          operador: Operador = Depends(operador_actual), db: Session = Depends(get_db),
          ahora: datetime = Depends(ahora_utc)):
    sesion, tutor = par
    try:
        hijo = _hijo(request, operador, tutor, alumno_id)
    except SinTPV:
        return _pagina(request, "tutor/resultado.html", {
            "titulo": "Pedir", "mensaje": MSG_SIN_TPV, "volver": "/", "detalles": [], "error": True})
    return _pagina_pedir(request, sesion, tutor, operador, db, hijo, ahora, fecha)


@router.post("/hijos/{alumno_id}/pedir/total")
def total_en_vivo(alumno_id: int, request: Request, csrf: str = Form(""),
                  form=Depends(_formulario), par: tuple[Sesion, Tutor] = Depends(tutor_listo),
                  operador: Operador = Depends(operador_actual)):
    """Fragmento HTMX: sólo el total de lo que el formulario trae ahora. No guarda nada ni
    reemplaza los campos (los +/− viven en el navegador): una respuesta tardía no pisa nada."""
    sesion, tutor = par
    exigir_csrf(csrf, sesion.csrf)
    try:
        _hijo(request, operador, tutor, alumno_id)
        menu = tutor_tpv.menu(request, operador, cacheado=True)
    except SinTPV:
        # Un error HTTP no lo cambia HTMX en la página: el total anterior se queda.
        return Response(MSG_SIN_MENU, status_code=503, headers=SIN_CACHE)
    return _pagina(request, "tutor/_total.html", {
        "total": _total(menu, _cantidades_tolerantes(form))})


@router.post("/hijos/{alumno_id}/pedir/revisar")
def revisar(alumno_id: int, request: Request, csrf: str = Form(""), fecha: str = Form(""),
            form=Depends(_formulario), par: tuple[Sesion, Tutor] = Depends(tutor_listo),
            operador: Operador = Depends(operador_actual), db: Session = Depends(get_db),
            ahora: datetime = Depends(ahora_utc)):
    sesion, tutor = par
    exigir_csrf(csrf, sesion.csrf)
    volver = f"/hijos/{alumno_id}/pedir"
    try:
        hijo = _hijo(request, operador, tutor, alumno_id)
        menu = tutor_tpv.menu(request, operador)
    except SinTPV:
        return _error(request, MSG_SIN_TPV, volver)
    cantidades = _cantidades_tolerantes(form)
    pares = [(k[5:], form.get(k)) for k in form.keys() if k.startswith("cant_")]
    items, error = validar_lineas(pares, menu, cero_omite=True)
    dia = _fecha(fecha)
    if dia is None:
        error = MSG_FECHA
    else:
        motivo = motivo_fecha_invalida(cargar_reglas(db, operador.id), dia, ahora)
        error = motivo or error
    if error:
        return _pagina_pedir(request, sesion, tutor, operador, db, hijo, ahora, fecha,
                             cantidades, error)
    cant = dict(items)
    en_menu = [(p, cant[p["id"]]) for p in menu if p["id"] in cant]  # en el orden del menú
    total = sum((p["precio"] * n for p, n in en_menu), Decimal(0))
    disponible = hijo.get("disponible")
    try:
        alcanza = Decimal(str(disponible)) >= total
    except (InvalidOperation, ValueError, TypeError):  # valor raro del TPV: decide el TPV
        alcanza = True
    return _pagina(request, "tutor/revisar_pedido.html", {
        "titulo": f"Revisa el pedido de {hijo['nombre']}",
        "lineas": [f"{n} × {p['nombre']} — {dinero(p['precio'] * n)}" for p, n in en_menu],
        "accion": f"/hijos/{alumno_id}/pedir/confirmar",
        "campos": {"fecha": dia.isoformat(), "producto_id": [str(p["id"]) for p, _ in en_menu],
                   "cantidad": [str(n) for _, n in en_menu], "llave": uuid.uuid4().hex},
        "boton": "Confirmar pedido", "volver": f"{volver}?fecha={dia.isoformat()}",
        "csrf": sesion.csrf, "para": hijo["nombre"], "dia": dia, "total": total,
        "disponible": disponible,
        "aviso": None if alcanza else f"No te alcanza el saldo de {hijo['nombre']} para este pedido."})


def _intento_incierto(db: Session, operador: Operador, tutor: Tutor, llave: str) -> bool:
    """¿Hubo ya un envío con esta llave que no obtuvo respuesta clara del TPV?"""
    return db.scalars(select(Bitacora.id).where(
        Bitacora.operador_id == operador.id, Bitacora.actor == f"tutor:{tutor.id}",
        Bitacora.operacion == "precompra", Bitacora.llave == llave,
        or_(Bitacora.http_status < 200, Bitacora.http_status >= 500,
            # 200 con cuerpo ilegible: igual de incierto (misma regla que el reintento)
            and_(Bitacora.http_status < 400, Bitacora.codigo_error.is_not(None))))).first() is not None


def _con_aviso(db, operador, tutor, llave, hijo, mensaje: str) -> str:
    """Si un envío anterior con esta llave quedó sin respuesta, un rechazo de ahora no prueba que
    aquél fallara: puede existir el pedido. Se le dice al tutor que lo revise."""
    if _intento_incierto(db, operador, tutor, llave):
        mensaje += (" Ojo: tu intento anterior no tuvo respuesta y puede haberse registrado. "
                    f"Revisa los movimientos de {hijo['nombre']} antes de volver a pedir.")
    return mensaje


def _mensaje_error(e: ErrorTPV, nombre: str) -> str:
    # El TPV real rechaza el saldo como 409 `rechazado` con «Saldo insuficiente de …» en el texto.
    if e.codigo == "saldo_insuficiente" or (
            e.codigo == "rechazado" and e.mensaje.startswith("Saldo insuficiente")):
        return f"No te alcanza el saldo de {nombre} para este pedido."
    return mensaje_tpv(e, "pedido")


@router.post("/hijos/{alumno_id}/pedir/confirmar")
def confirmar(alumno_id: int, request: Request, csrf: str = Form(""), fecha: str = Form(""),
              llave: str = Form(""), producto_id: list[str] = Form([]),
              cantidad: list[str] = Form([]),
              par: tuple[Sesion, Tutor] = Depends(tutor_listo),
              operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, tutor = par
    exigir_csrf(csrf, sesion.csrf)
    volver = f"/hijos/{alumno_id}/pedir"
    try:
        hijo = _hijo(request, operador, tutor, alumno_id)
        menu = tutor_tpv.menu(request, operador)
    except SinTPV:
        # Si un intento anterior quedó sin respuesta, empezar de nuevo daría OTRA llave y un
        # segundo pedido: se conserva el mismo envío para reintentarlo tal cual.
        if _LLAVE.fullmatch(llave) and _fecha(fecha) and len(producto_id) == len(cantidad):
            return _pagina(request, "tutor/confirmar.html", {
                "titulo": "Confirma tu pedido", "lineas": [], "aviso": MSG_SIN_TPV,
                "accion": f"/hijos/{alumno_id}/pedir/confirmar", "csrf": sesion.csrf,
                "campos": {"fecha": fecha, "producto_id": producto_id, "cantidad": cantidad,
                           "llave": llave},
                "boton": "Reintentar", "volver": f"/hijos/{alumno_id}"})
        return _error(request, MSG_SIN_TPV, volver)
    if not _LLAVE.fullmatch(llave):
        return _error(request, MSG_LLAVE, volver)
    if len(producto_id) != len(cantidad):
        return _error(request, MSG_CANTIDAD, volver)
    items, error = validar_lineas(list(zip(producto_id, cantidad)), menu, cero_omite=False)
    dia = _fecha(fecha)
    if error is None and dia is None:
        error = MSG_FECHA
    if error is None:
        # Con la hora de AHORA (tras las lecturas), no la de la revisión.
        error = motivo_fecha_invalida(cargar_reglas(db, operador.id), dia, ahora_fresca(request))
    if error:
        return _error(request, _con_aviso(db, operador, tutor, llave, hijo, error), volver)
    cuerpo = {"alumno_id": alumno_id, "fecha_entrega": dia.isoformat(), "llave": llave,
              "items": [{"producto_id": pid, "cantidad": n} for pid, n in items]}
    try:
        r = escribir_tutor(db, request, operador, tutor, "precompra", cuerpo)
    except ErrorTPV as e:
        db.commit()  # la bitácora del intento fallido es verdad
        if not 400 <= e.status < 500:
            # Sin respuesta clara: puede que sí entrara. Se reintenta con LA MISMA llave.
            return _pagina(request, "tutor/confirmar.html", _contexto_confirmar(
                alumno_id, sesion, hijo, dia, items, menu, llave, MSG_SIN_RESPUESTA))
        return _error(request, _con_aviso(db, operador, tutor, llave, hijo,
                                          _mensaje_error(e, hijo["nombre"])), volver)
    db.commit()
    pedido = r.get("pedido") if isinstance(r.get("pedido"), dict) else {}
    alumno = r.get("alumno") if isinstance(r.get("alumno"), dict) else {}
    if not pedido.get("nombre"):
        return _error(request, "El TPV respondió algo inesperado. Revisa los movimientos de "
                      f"{hijo['nombre']} antes de volver a pedir.", f"/hijos/{alumno_id}")
    detalles = []
    if pedido.get("nombre"):
        detalles.append(f"Folio: {pedido['nombre']}")
    detalles.append(f"Para el día: {fmt_fecha(pedido.get('fecha_entrega') or dia)}")
    if "total" in pedido:
        detalles.append(f"Total: {dinero(pedido['total'])}")
    if "disponible" in alumno:
        detalles.append(f"Disponible de {hijo['nombre']}: {dinero(alumno['disponible'])}")
    estado = pedido.get("estado")
    if estado == "retenida":
        titulo, mensaje = "Pedido listo", f"Quedó apartado el pedido de {hijo['nombre']}."
    else:
        # Reenvío de un pedido que ya cambió (cancelado, entregado…): la idempotencia lo devuelve tal cual.
        titulo = "Este pedido ya se había hecho"
        mensaje = f"Estado actual: {ESTADOS_PEDIDO.get(estado, 'desconocido')}."
    return _pagina(request, "tutor/resultado.html", {
        "titulo": titulo, "mensaje": mensaje, "detalles": detalles,
        "volver": f"/hijos/{alumno_id}", "error": estado != "retenida"})


def _contexto_confirmar(alumno_id, sesion, hijo, dia, items, menu, llave, aviso) -> dict:
    nombres = {p["id"]: p["nombre"] for p in menu}
    return {
        "titulo": f"Confirma el pedido de {hijo['nombre']}",
        "lineas": [f"{n} × {nombres[pid]}" for pid, n in items] + [f"Para el día: {fmt_fecha(dia)}"],
        "accion": f"/hijos/{alumno_id}/pedir/confirmar",
        "campos": {"fecha": dia.isoformat(), "producto_id": [str(pid) for pid, _ in items],
                   "cantidad": [str(n) for _, n in items], "llave": llave},
        "boton": "Confirmar pedido", "volver": f"/hijos/{alumno_id}/pedir?fecha={dia.isoformat()}",
        "csrf": sesion.csrf, "aviso": aviso}
