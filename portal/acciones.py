"""Núcleo de acciones del tutor, sin HTTP: lo usan el bot de WhatsApp y (después) el servidor MCP.

Compone los MISMOS helpers que las rutas web (validación, hora límite, idempotencia por llave,
bitácora, intentos sin respuesta), así que las reglas no se duplican; sólo cambia la salida: texto
plano para el papá en vez de una página. Cada escritura devuelve un `Resultado`:
- `ok`: entró; `texto` dice qué pasó.
- `incierto`: no sabemos si entró; se reintenta con LA MISMA llave.
- `rechazo`: no entró; `texto` dice por qué.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, HTTPException
from sqlalchemy.orm import Session

from portal import recargas, tutor_tpv
from portal.formato import ESTADOS_PEDIDO, TIPOS_CREDENCIAL, dinero, fecha as fmt_fecha
from portal.models import Operador, Recarga, Tutor
from portal.reglas import (
    ahora_utc, cargar_reglas, fechas_disponibles, motivo_fecha_invalida)
from portal.rutas.pedidos import OP_CANCELAR, OP_PEDIR, motivo_no_cancelable, validar_lineas
from portal.tutor_tpv import (
    SinTPV, con_aviso, enviar_escritura, intento_incierto, ya_registrado)

MSG_INCIERTO = ("No pudimos confirmar con la cafetería si quedó registrado. Responde 1 para "
                "reintentar (no se duplica).")


class Ctx:
    """Lo único que los helpers de `tutor_tpv` toman del `Request`: la app (transportes, ajustes,
    caché del menú)."""

    def __init__(self, app: FastAPI):
        self.app = app


@dataclass
class Resultado:
    estado: str  # "ok" | "incierto" | "rechazo"
    texto: str
    datos: dict = field(default_factory=dict)


def ahora(app: FastAPI) -> datetime:
    """La hora de las reglas, respetando la sustitución que hagan las pruebas."""
    return app.dependency_overrides.get(ahora_utc, ahora_utc)()


# -- lecturas ---------------------------------------------------------------------------------
def hijos(app: FastAPI, operador: Operador, tutor: Tutor) -> list[dict]:
    """Fichas del TPV con `id` entero. Lanza SinTPV."""
    return [h for h in tutor_tpv.hijos(Ctx(app), operador, tutor)
            if isinstance(h, dict) and isinstance(h.get("id"), int)
            and not isinstance(h["id"], bool)]


def dias_para_pedir(db: Session, app: FastAPI, operador: Operador) -> list[date]:
    return fechas_disponibles(cargar_reglas(db, operador.id), ahora(app))


def menu(app: FastAPI, operador: Operador) -> list[dict]:
    return tutor_tpv.menu(Ctx(app), operador)


def pedidos_por_recoger(app: FastAPI, operador: Operador, tutor: Tutor,
                        alumno_id: int) -> list[dict]:
    lista = [p for p in tutor_tpv.pedidos(Ctx(app), operador, tutor, alumno_id)
             if p.get("estado") == "retenida"]
    return sorted(lista, key=lambda p: (p["fecha_entrega"], p["id"]))


# -- escrituras -------------------------------------------------------------------------------
def _escribir(db, app, operador, tutor, operacion, cuerpo, llave, hijo, reintento, que):
    estado, r = enviar_escritura(db, Ctx(app), operador, tutor, operacion, cuerpo, llave, hijo,
                                 reintento, que)
    if estado == "incierto":
        return Resultado("incierto", MSG_INCIERTO), None
    if estado == "rechazo":
        return Resultado("rechazo", r), None
    return None, r


def pedir(db: Session, app: FastAPI, operador: Operador, tutor: Tutor, hijo: dict, dia: date,
          items: list[tuple[int, int]], llave: str) -> Resultado:
    try:
        carta = menu(app, operador)
    except SinTPV:
        return Resultado("incierto", tutor_tpv.MSG_SIN_MENU + " Responde 1 para reintentar.")
    ya_entro = ya_registrado(db, operador, tutor, llave, OP_PEDIR)
    lineas, error = validar_lineas([(str(pid), str(n)) for pid, n in items],
                                   [{"id": pid} for pid, _ in items] if ya_entro else carta,
                                   cero_omite=False)
    if error is None and not ya_entro:
        error = motivo_fecha_invalida(cargar_reglas(db, operador.id), dia, ahora(app))
    if error:
        return Resultado("rechazo", con_aviso(db, operador, tutor, llave, hijo, error, OP_PEDIR))
    cuerpo = {"alumno_id": hijo["id"], "fecha_entrega": dia.isoformat(), "llave": llave,
              "items": [{"producto_id": pid, "cantidad": n} for pid, n in lineas]}
    fallo, r = _escribir(db, app, operador, tutor, OP_PEDIR, cuerpo, llave, hijo, "volver a pedir",
                         "los movimientos")
    if fallo:
        return fallo
    pedido = r.get("pedido") if isinstance(r.get("pedido"), dict) else {}
    alumno = r.get("alumno") if isinstance(r.get("alumno"), dict) else {}
    if not pedido.get("nombre"):
        return Resultado("rechazo", "La cafetería respondió algo inesperado. Revisa los movimientos "
                                    f"de {hijo['nombre']} antes de volver a pedir.")
    partes = [f"Pedido {pedido['nombre']} de {hijo['nombre']} para el "
              f"{fmt_fecha(pedido.get('fecha_entrega') or dia)}."]
    if "total" in pedido:
        partes.append(f"Total: {dinero(pedido['total'])}.")
    if "disponible" in alumno:
        partes.append(f"Disponible: {dinero(alumno['disponible'])}.")
    if pedido.get("estado") != "retenida":
        partes.insert(0, "Este pedido ya se había hecho; estado actual: "
                         f"{ESTADOS_PEDIDO.get(pedido.get('estado'), 'desconocido')}.")
    else:
        partes.insert(0, "¡Listo!")
    return Resultado("ok", " ".join(partes), {"pedido": pedido})


def cancelar(db: Session, app: FastAPI, operador: Operador, tutor: Tutor, hijo: dict,
             pedido_id: int, llave: str) -> Resultado:
    try:
        lista = tutor_tpv.pedidos(Ctx(app), operador, tutor, hijo["id"])
    except SinTPV:
        return Resultado("incierto", tutor_tpv.MSG_SIN_PEDIDOS + " Responde 1 para reintentar.")
    pedido = next((p for p in lista if p["id"] == pedido_id), None)
    if pedido is None:
        return Resultado("rechazo", "Ese pedido ya no aparece.")
    ya_entro = ya_registrado(db, operador, tutor, llave, OP_CANCELAR) or (
        pedido.get("estado") == "cancelada"
        and intento_incierto(db, operador, tutor, llave, OP_CANCELAR))
    if not ya_entro:
        motivo = motivo_no_cancelable(cargar_reglas(db, operador.id), pedido, ahora(app))
        if motivo:
            return Resultado("rechazo", con_aviso(db, operador, tutor, llave, hijo, motivo,
                                                  OP_CANCELAR, "volver a intentarlo"))
    fallo, r = _escribir(db, app, operador, tutor, OP_CANCELAR,
                         {"pedido_id": pedido_id, "llave": llave}, llave, hijo,
                         "volver a intentarlo", "los movimientos")
    if fallo:
        return fallo
    nuevo = r.get("pedido") if isinstance(r.get("pedido"), dict) else {}
    alumno = r.get("alumno") if isinstance(r.get("alumno"), dict) else {}
    if nuevo.get("estado") != "cancelada" or not nuevo.get("nombre"):
        return Resultado("rechazo", "La cafetería respondió algo inesperado. Revisa los movimientos "
                                    f"de {hijo['nombre']} antes de volver a intentarlo.")
    texto = f"Listo, cancelaste el pedido {nuevo['nombre']} de {hijo['nombre']}."
    if "disponible" in alumno and not ya_entro:
        texto += f" Disponible: {dinero(alumno['disponible'])}."
    return Resultado("ok", texto)


def transferir(db: Session, app: FastAPI, operador: Operador, tutor: Tutor, origen: dict,
               destino: dict, importe: Decimal, llave: str) -> Resultado:
    from portal.rutas.transferir import OP, _alcanza
    ya_entro = ya_registrado(db, operador, tutor, llave, OP) or intento_incierto(
        db, operador, tutor, llave, OP)
    pareja = {"nombre": f"{origen['nombre']} y {destino['nombre']}"}
    if origen["id"] == destino["id"]:
        return Resultado("rechazo", "Elige dos hijos distintos.")
    if not ya_entro and not _alcanza(origen, importe):
        return Resultado("rechazo", con_aviso(
            db, operador, tutor, llave, pareja,
            f"{origen['nombre']} no tiene tanto: su disponible es "
            f"{dinero(origen.get('disponible'))}.", OP, "volver a transferir"))
    fallo, r = _escribir(db, app, operador, tutor, OP,
                         {"origen_id": origen["id"], "destino_id": destino["id"],
                          "importe": float(importe), "llave": llave},
                         llave, pareja, "volver a transferir", "los movimientos")
    if fallo:
        return fallo
    nuevos = {a.get("id"): a for a in r.get("alumnos") or [] if isinstance(a, dict)} \
        if isinstance(r.get("alumnos"), list) else {}
    o, d = nuevos.get(origen["id"]), nuevos.get(destino["id"])
    if not o or not d or "disponible" not in o or "disponible" not in d:
        return Resultado("rechazo", f"La cafetería respondió algo inesperado. Revisa los "
                                    f"movimientos de {pareja['nombre']} antes de volver a transferir.")
    return Resultado("ok", f"Listo, pasaste {dinero(importe)} de {origen['nombre']} a "
                           f"{destino['nombre']}. Disponible de {origen['nombre']}: "
                           f"{dinero(o['disponible'])}; de {destino['nombre']}: "
                           f"{dinero(d['disponible'])}.")


def _cambiar(db, app, operador, tutor, hijo, operacion, cuerpo, llave, leer, objetivo,
             texto_listo) -> Resultado:
    """Permiso y credenciales: como `_hacer_cambio` de la web. Se escribe aunque ya esté así (la
    llave queda consumida: un reintento viejo no deshace un cambio posterior)."""
    ya_esta = leer(hijo) is objetivo
    entro = ya_registrado(db, operador, tutor, llave, operacion) or intento_incierto(
        db, operador, tutor, llave, operacion)
    fallo, r = _escribir(db, app, operador, tutor, operacion, {**cuerpo, "llave": llave}, llave,
                         hijo, "volver a intentarlo", "la ficha")
    if fallo:
        return fallo
    alumno = r.get("alumno") if isinstance(r.get("alumno"), dict) else {}
    if leer(alumno) is not objetivo:
        return Resultado("rechazo", "La cafetería respondió algo inesperado. Revisa la ficha de "
                                    f"{hijo['nombre']} en el portal.")
    if entro:
        try:
            actual = leer(tutor_tpv.hijo_o_404(Ctx(app), operador, tutor, hijo["id"])[0])
        except (SinTPV, HTTPException):
            actual = None
        if actual is not objetivo:
            return Resultado("ok", f"Tu cambio se registró, pero la ficha de {hijo['nombre']} ya "
                                   "no está así: puede que otro tutor lo haya cambiado.")
    if ya_esta and not entro:
        return Resultado("ok", f"Ya estaba así: {texto_listo}")
    return Resultado("ok", f"Listo: {texto_listo}")


def permiso(db: Session, app: FastAPI, operador: Operador, tutor: Tutor, hijo: dict,
            permitir: bool, llave: str) -> Resultado:
    def leer(a):
        v = a.get("compra_sin_pedido")
        return v if isinstance(v, bool) else None
    texto = (f"{hijo['nombre']} podrá comprar sin pedido." if permitir
             else f"{hijo['nombre']} sólo podrá recoger lo que pidas.")
    return _cambiar(db, app, operador, tutor, hijo, "alumno/permiso",
                    {"alumno_id": hijo["id"], "compra_sin_pedido": permitir}, llave, leer,
                    permitir, texto)


def credencial(db: Session, app: FastAPI, operador: Operador, tutor: Tutor, hijo: dict,
               cred: dict, bloquear: bool, llave: str) -> Resultado:
    cred_id = cred["id"]

    def leer(a):
        for x in a.get("credenciales") or []:
            if isinstance(x, dict) and x.get("id") == cred_id and isinstance(x.get("bloqueada"), bool):
                return x["bloqueada"]
        return None
    etiqueta = TIPOS_CREDENCIAL.get(cred.get("tipo"), "Credencial")
    texto = (f"{etiqueta} de {hijo['nombre']} bloqueada: nadie podrá pagar con ella."
             if bloquear else f"{etiqueta} de {hijo['nombre']} desbloqueada.")
    accion = "bloquear" if bloquear else "desbloquear"
    return _cambiar(db, app, operador, tutor, hijo, f"credencial/{accion}",
                    {"credencial_id": cred_id}, llave, leer, bloquear, texto)


def recargar(db: Session, app: FastAPI, operador: Operador, tutor: Tutor, reparto: list[dict],
             total: Decimal, llave: str) -> Resultado:
    """La liga de pago de MercadoPago (la acreditación llega sola, por el aviso de MP)."""
    if not operador.mp_access_token_cif:
        return Resultado("rechazo", "Las recargas no están disponibles por ahora.")
    try:
        r: Recarga = recargas.crear(db, app, operador, tutor, reparto, total, llave,
                                    f"https://{operador.host}")
    except recargas.ErrorMP:
        return Resultado("incierto", "No pudimos abrir Mercado Pago. No se te cobró nada: "
                                     "responde 1 para reintentar.")
    return Resultado("ok", f"Paga {dinero(total)} aquí: {r.init_point}\nLa liga vence en 30 "
                           "minutos. Te avisamos por aquí cuando se acredite.", {"recarga_id": r.id})
