"""Pedir con fecha: elegir día, armar el carrito, revisar y confirmar (precompra del TPV).

Lo que se muestra en dinero sale de los precios del menú sólo para orientar; el TPV pone los
precios reales y la página de resultado muestra SU total.
"""
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from portal import tutor_tpv
from portal.db import get_db
from portal.formato import ESTADOS_PEDIDO, dinero, fecha as fmt_fecha
from portal.models import Bitacora, Operador, Sesion, Tutor
from portal.reglas import (
    Reglas, ahora_fresca, ahora_local, ahora_utc, cargar_reglas, fechas_disponibles,
    motivo_fecha_invalida, motivos_sin_servicio, puede_cancelar, semanas_para_pedir)
from portal.sesiones import exigir_csrf, operador_actual, tutor_listo
from portal.tutor_tpv import (
    LLAVE, MSG_SIN_MENU, MSG_SIN_PEDIDOS, MSG_SIN_TPV, SIN_CACHE, SinTPV, con_aviso,
    enviar_escritura, intento_incierto, msg_llave, ya_registrado)
from portal.web import templates

router = APIRouter()

OP_PEDIR = "precompra"

# Tope por producto: un pedido de comedor escolar no pasa de unas cuantas piezas por línea, y un
# tope corto frena el error de dedo (200 en vez de 2).
MAX_POR_LINEA = 20
MSG_CANTIDAD = f"Cantidad no válida: usa números enteros del 1 al {MAX_POR_LINEA} por producto."
MSG_VACIO = "Elige al menos un producto."
MSG_FECHA = "Fecha no válida."
MSG_SIN_RESPUESTA = ("No se pudo contactar al TPV. Tu pedido puede haberse registrado: vuelve a "
                     "tocar «Confirmar pedido» (no se duplica) o revisa los movimientos de tu hijo.")
_ENTERO = re.compile(r"[0-9]{1,9}")


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


def _error(request, mensaje: str, volver: str, titulo: str = "No se pudo hacer el pedido"):
    return _pagina(request, "tutor/resultado.html", {
        "titulo": titulo, "mensaje": mensaje, "volver": volver, "detalles": [], "error": True})


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
           "error_tpv": None,
           "semanas": semanas_para_pedir(reglas, ahora, motivos_sin_servicio(db, operador.id))}
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
        # La semana que se abre es la del día elegido.
        ctx["semana_actual"] = next((i for i, sem in enumerate(ctx["semanas"])
                                     if any(d.fecha == ctx["fecha"] for d in sem.dias)), 0)
        try:
            menu = tutor_tpv.menu(request, operador, cacheado=True)
        except SinTPV:
            ctx["error_tpv"] = MSG_SIN_MENU
        else:
            ctx["grupos"] = _agrupar(menu)
            ctx["total"] = _total(menu, ctx["cantidades"])
    return templates.TemplateResponse(request, "tutor/pedir.html", ctx, headers=SIN_CACHE)


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
    # «Seguir pidiendo» regresa con lo que ya se había elegido (cant_{id} en la URL).
    return _pagina_pedir(request, sesion, tutor, operador, db, hijo, ahora, fecha,
                         _cantidades_tolerantes(request.query_params))


def _url_pedir(alumno_id: int, dia: date, items) -> str:
    """Regreso a armar el pedido con el día y las cantidades que ya se habían elegido."""
    return (f"/hijos/{alumno_id}/pedir?"
            + urlencode([("fecha", dia.isoformat())] + [(f"cant_{pid}", n) for pid, n in items]))


@router.post("/hijos/{alumno_id}/pedir/total")
def total_en_vivo(alumno_id: int, request: Request, csrf: str = Form(""),
                  form=Depends(_formulario), par: tuple[Sesion, Tutor] = Depends(tutor_listo),
                  operador: Operador = Depends(operador_actual)):
    """Fragmento HTMX: sólo el total de lo que el formulario trae ahora. No guarda nada ni
    reemplaza los campos (los +/− viven en el navegador): una respuesta tardía no pisa nada."""
    sesion, _ = par
    exigir_csrf(csrf, sesion.csrf)
    # No se lee `tutor/alumnos` en cada +/−: el total sale del menú (igual para todos) y no revela
    # nada del hijo; la propiedad del hijo se exige al revisar y al confirmar.
    try:
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
        "boton": "Confirmar pedido", "volver": _url_pedir(alumno_id, dia, items),
        "volver_texto": "Seguir pidiendo", "cancelar": f"/hijos/{alumno_id}",
        "titulo_fino": "Revisa el", "titulo_fuerte": "pedido",
        "renglones": [(f"{n} × {p['nombre']}", dinero(p["precio"] * n)) for p, n in en_menu],
        "csrf": sesion.csrf, "para": hijo["nombre"], "dia": dia, "total": total,
        "disponible": disponible,
        "aviso": None if alcanza else f"No te alcanza el saldo de {hijo['nombre']} para este pedido."})


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
        if LLAVE.fullmatch(llave) and _fecha(fecha) and len(producto_id) == len(cantidad):
            return _pagina(request, "tutor/confirmar.html", {
                "titulo": "Confirma tu pedido", "lineas": [], "aviso": MSG_SIN_TPV,
                "accion": f"/hijos/{alumno_id}/pedir/confirmar", "csrf": sesion.csrf,
                "campos": {"fecha": fecha, "producto_id": producto_id, "cantidad": cantidad,
                           "llave": llave},
                "boton": "Reintentar", "volver": f"/hijos/{alumno_id}"})
        return _error(request, MSG_SIN_TPV, volver)
    if not LLAVE.fullmatch(llave):
        return _error(request, msg_llave(OP_PEDIR), volver)
    if len(producto_id) != len(cantidad):
        return _error(request, MSG_CANTIDAD, volver)
    # Si esta llave ya entró (200 en bitácora), un doble toque o una página vieja que cruza la hora
    # límite o cambia el menú debe ver el pedido que ya existe: se deja que el TPV lo repita.
    ya_entro = ya_registrado(db, operador, tutor, llave, OP_PEDIR)
    items, error = validar_lineas(list(zip(producto_id, cantidad)),
                                  [{"id": int(x)} for x in producto_id if _ENTERO.fullmatch(x.strip())]
                                  if ya_entro else menu, cero_omite=False)
    dia = _fecha(fecha)
    if error is None and dia is None:
        error = MSG_FECHA
    if error is None and not ya_entro:
        # Con la hora de AHORA (tras las lecturas), no la de la revisión.
        error = motivo_fecha_invalida(cargar_reglas(db, operador.id), dia, ahora_fresca(request))
    if error:
        return _error(request, con_aviso(db, operador, tutor, llave, hijo, error, OP_PEDIR), volver)
    cuerpo = {"alumno_id": alumno_id, "fecha_entrega": dia.isoformat(), "llave": llave,
              "items": [{"producto_id": pid, "cantidad": n} for pid, n in items]}
    estado_envio, r = enviar_escritura(db, request, operador, tutor, OP_PEDIR, cuerpo, llave, hijo,
                                       "volver a pedir")
    if estado_envio == "incierto":
        # Sin respuesta clara: puede que sí entrara. Se reintenta con LA MISMA llave.
        return _pagina(request, "tutor/confirmar.html", _contexto_confirmar(
            alumno_id, sesion, hijo, dia, items, menu, llave, MSG_SIN_RESPUESTA))
    if estado_envio == "rechazo":
        return _error(request, r, volver)
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
    lineas = pedido.get("lineas") if isinstance(pedido.get("lineas"), list) else []
    ticket = {
        "encabezado": f"PEDIDO {pedido['nombre']} · {fmt_fecha(pedido.get('fecha_entrega') or dia)}",
        "lineas": [f"{_cantidad(l)} × {l.get('producto') or 'Producto'}"
                   for l in lineas if isinstance(l, dict)],
        "total": dinero(pedido["total"]) if "total" in pedido else None}
    return _pagina(request, "tutor/resultado.html", {
        "titulo": titulo, "mensaje": mensaje, "ticket": ticket,
        # Lo que no cabe en el ticket (el disponible después del pedido) va debajo.
        "detalles": [d for d in detalles if d.startswith("Disponible")],
        **({"titulo_fino": "Pedido", "titulo_fuerte": "listo"} if estado == "retenida" else {}),
        "volver": f"/hijos/{alumno_id}", "error": estado != "retenida"})


def _contexto_confirmar(alumno_id, sesion, hijo, dia, items, menu, llave, aviso) -> dict:
    nombres = {p["id"]: p["nombre"] for p in menu}
    return {
        "titulo": f"Confirma el pedido de {hijo['nombre']}",
        "lineas": [f"{n} × {nombres.get(pid, f'Producto {pid}')}" for pid, n in items] + [f"Para el día: {fmt_fecha(dia)}"],
        "accion": f"/hijos/{alumno_id}/pedir/confirmar",
        "campos": {"fecha": dia.isoformat(), "producto_id": [str(pid) for pid, _ in items],
                   "cantidad": [str(n) for _, n in items], "llave": llave},
        # Sin respuesta del TPV el pedido pudo entrar: aquí NO se ofrece «Cancelar» (no cancelaría
        # nada) ni «Seguir pidiendo» (otra llave = otro pedido). Sólo reintentar con la MISMA llave
        # o ir a ver los pedidos del hijo. Hallazgo de Codex.
        "boton": "Confirmar pedido", "volver": f"/hijos/{alumno_id}", "volver_texto": "Ver pedidos",
        "titulo_fino": "Confirma el", "titulo_fuerte": "pedido", "subtitulo": f"de {hijo['nombre']}",
        "csrf": sesion.csrf, "aviso": aviso}



# ------------------------------------------------------------------- cancelar

OP_CANCELAR = "precompra/cancelar"
TIT_CANCELAR = "No se pudo cancelar el pedido"
MSG_SIN_RESPUESTA_CANCELAR = (
    "No se pudo contactar al TPV. Tu cancelación puede haberse registrado: vuelve a tocar "
    "«Cancelar pedido» (no se repite) o revisa los movimientos de tu hijo.")


def _pedido_del_hijo(pedidos: list[dict], pedido_id: int) -> dict:
    """Sólo vale un pedido que el TPV lista para ESTE hijo; si no, 404 sin tocar el TPV."""
    for p in pedidos:
        if p["id"] == pedido_id:
            return p
    raise HTTPException(404)


def motivo_no_cancelable(reglas: Reglas, pedido: dict, ahora: datetime) -> str | None:
    estado = pedido.get("estado")
    if estado == "cancelada":
        return "Este pedido ya estaba cancelado."
    if estado != "retenida":
        return f"Este pedido ya no se puede cancelar ({ESTADOS_PEDIDO.get(estado, 'estado desconocido')})."
    if puede_cancelar(reglas, pedido["fecha_entrega"], ahora):
        return None
    if pedido["fecha_entrega"] == ahora_local(reglas, ahora).date():
        return f"Ya pasó la hora límite para cancelar el pedido de hoy ({reglas.hora_limite:%H:%M})."
    return "Este pedido es de un día que ya pasó."


def _cantidad(l: dict) -> str:
    try:
        n = Decimal(str(l.get("cantidad")))
        return str(int(n)) if n == n.to_integral_value() else str(n.normalize())
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return "?"


def _contexto_cancelar(alumno_id, sesion, hijo, pedido, llave, aviso=None) -> dict:
    lineas = [f"Pedido {pedido.get('nombre') or pedido['id']}",
              f"Para el día: {fmt_fecha(pedido['fecha_entrega'])}"]
    lineas += [f"{_cantidad(l)} × {l.get('producto') or 'Producto'}" for l in pedido["lineas"]]
    if "total" in pedido:
        lineas.append(f"Total: {dinero(pedido['total'])}")
    nota = f"Al cancelar, ese dinero vuelve al saldo disponible de {hijo['nombre']}."
    lineas.append(nota)
    ticket = {
        "encabezado": f"PEDIDO {pedido.get('nombre') or pedido['id']} · {hijo['nombre'].upper()}",
        "lineas": [fmt_fecha(pedido["fecha_entrega"])]
                  + [f"{_cantidad(l)} × {l.get('producto') or 'Producto'}" for l in pedido["lineas"]],
        "total": dinero(pedido["total"]) if "total" in pedido else None}
    return {
        "titulo": f"Cancelar el pedido de {hijo['nombre']}", "lineas": lineas, "aviso": aviso,
        "titulo_fino": "Cancelar el", "titulo_fuerte": "pedido", "ticket": ticket, "nota": nota,
        "peligro": True,
        "accion": f"/hijos/{alumno_id}/pedidos/{pedido['id']}/cancelar", "csrf": sesion.csrf,
        "campos": {"llave": llave, "folio": str(pedido.get("nombre") or "")}, "boton": "Cancelar pedido",
        "volver": f"/hijos/{alumno_id}"}


@router.get("/hijos/{alumno_id}/pedidos/{pedido_id}/cancelar")
def cancelar_confirmacion(alumno_id: int, pedido_id: int, request: Request,
                          par: tuple[Sesion, Tutor] = Depends(tutor_listo),
                          operador: Operador = Depends(operador_actual),
                          db: Session = Depends(get_db), ahora: datetime = Depends(ahora_utc)):
    sesion, tutor = par
    volver = f"/hijos/{alumno_id}"
    try:
        hijo = _hijo(request, operador, tutor, alumno_id)
        pedidos = tutor_tpv.pedidos(request, operador, tutor, alumno_id)
    except SinTPV:
        return _error(request, MSG_SIN_PEDIDOS, volver, TIT_CANCELAR)
    pedido = _pedido_del_hijo(pedidos, pedido_id)
    motivo = motivo_no_cancelable(cargar_reglas(db, operador.id), pedido, ahora)
    if motivo:
        return _error(request, motivo, volver, TIT_CANCELAR)
    return _pagina(request, "tutor/confirmar.html", _contexto_cancelar(
        alumno_id, sesion, hijo, pedido, uuid.uuid4().hex))


@router.post("/hijos/{alumno_id}/pedidos/{pedido_id}/cancelar")
def cancelar(alumno_id: int, pedido_id: int, request: Request, csrf: str = Form(""),
             llave: str = Form(""), folio: str = Form("", max_length=40),
             par: tuple[Sesion, Tutor] = Depends(tutor_listo),
             operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, tutor = par
    exigir_csrf(csrf, sesion.csrf)
    volver = f"/hijos/{alumno_id}"
    try:
        hijo = _hijo(request, operador, tutor, alumno_id)
        pedidos = tutor_tpv.pedidos(request, operador, tutor, alumno_id)
    except SinTPV:
        # Un intento anterior pudo quedar sin respuesta: se conserva la llave para repetirlo tal cual.
        # El folio viaja en el formulario sólo para mostrarlo (sin la lista del TPV no se conoce);
        # nunca decide nada.
        if LLAVE.fullmatch(llave):
            folio = folio.strip()
            return _pagina(request, "tutor/confirmar.html", {
                "titulo": "Cancelar pedido",
                "lineas": [f"Pedido {folio}" if folio else "El pedido que ibas a cancelar"],
                "aviso": MSG_SIN_PEDIDOS,
                "accion": f"/hijos/{alumno_id}/pedidos/{pedido_id}/cancelar", "csrf": sesion.csrf,
                "campos": {"llave": llave, "folio": folio}, "boton": "Reintentar", "volver": volver})
        return _error(request, MSG_SIN_PEDIDOS, volver, TIT_CANCELAR)
    pedido = _pedido_del_hijo(pedidos, pedido_id)
    if not LLAVE.fullmatch(llave):
        return _error(request, msg_llave(OP_CANCELAR), volver, TIT_CANCELAR)
    # Con la llave ya registrada (200), o ya cancelado tras un intento sin respuesta, el TPV
    # repite su respuesta guardada: un doble toque ve el resultado aunque ya pasara la hora límite.
    ya_entro = ya_registrado(db, operador, tutor, llave, OP_CANCELAR) or (
        pedido.get("estado") == "cancelada" and intento_incierto(db, operador, tutor, llave, OP_CANCELAR))
    if not ya_entro:
        # Con la hora de AHORA (tras las lecturas), no la de la confirmación.
        motivo = motivo_no_cancelable(cargar_reglas(db, operador.id), pedido, ahora_fresca(request))
        if motivo:
            return _error(request, con_aviso(db, operador, tutor, llave, hijo, motivo, OP_CANCELAR,
                                              "volver a intentarlo"), volver, TIT_CANCELAR)
    estado, r = enviar_escritura(
        db, request, operador, tutor, OP_CANCELAR, {"pedido_id": pedido_id, "llave": llave}, llave,
        hijo, "volver a intentarlo")
    if estado == "incierto":
        return _pagina(request, "tutor/confirmar.html", _contexto_cancelar(
            alumno_id, sesion, hijo, pedido, llave, MSG_SIN_RESPUESTA_CANCELAR))
    if estado == "rechazo":
        return _error(request, r, volver, TIT_CANCELAR)
    nuevo = r.get("pedido") if isinstance(r.get("pedido"), dict) else {}
    alumno = r.get("alumno") if isinstance(r.get("alumno"), dict) else {}
    if nuevo.get("estado") != "cancelada" or not nuevo.get("nombre"):
        return _error(request, "El TPV respondió algo inesperado. Revisa los movimientos de "
                      f"{hijo['nombre']} antes de volver a intentarlo.", volver, TIT_CANCELAR)
    detalles = [f"Folio: {nuevo['nombre']}"]
    # Un repetido recibe la respuesta GUARDADA del TPV (con el saldo de aquel momento); la ficha
    # leída al empezar esta petición ya es posterior a la cancelación y trae el saldo de hoy.
    fuente = hijo if ya_entro and "disponible" in hijo else alumno
    if "disponible" in fuente:
        detalles.append(f"Disponible de {hijo['nombre']}: {dinero(fuente['disponible'])}")
    return _pagina(request, "tutor/resultado.html", {
        "titulo": f"Pedido {nuevo['nombre']} cancelado",
        "mensaje": f"Cancelaste el pedido de {hijo['nombre']}; su dinero ya está disponible.",
        "detalles": detalles, "volver": volver, "error": False})
