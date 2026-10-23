"""Servidor MCP del portal (`/mcp`): las acciones del papá para una IA que entienda lenguaje natural.

Autenticación: `Authorization: Bearer <token>` del operador (se resuelve por Host); sólo se guarda
su sha256. Quien tiene el token afirma qué papá escribe con `telefono` (número mexicano, igual que
el bot). Las acciones que mueven dinero o cambian algo van en DOS pasos impuestos por el servidor:
`preparar_*` valida y devuelve un resumen y un `folio`; sólo `confirmar(folio)` ejecuta, tal cual se
preparó, una vez. La IA debe mostrar el resumen al papá y esperar su «sí» antes de confirmar.
Mismo núcleo que la web y el bot (`portal.acciones`): mismas reglas, mismas llaves.
"""
import hashlib
import hmac
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI, HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from sqlalchemy.orm import Session

from portal import acciones, bot, candado, recargas
from portal.formato import TIPOS_CREDENCIAL, dinero, parse_importe
from portal.models import McpConfirmacion, Operador, Recarga, Tutor
from portal.reglas import cargar_reglas, motivo_fecha_invalida
from portal.rutas.pedidos import motivo_no_cancelable, validar_lineas
from portal.tutor_tpv import SinTPV

VIGENCIA_FOLIO = timedelta(minutes=10)
ESPERA_CANDADO = 30.0  # s que espera una confirmación a que termine otra del mismo folio

INSTRUCCIONES = """Portal de la cafetería escolar. Atiende a un papá identificado por su teléfono.
Consulta con `hijos`, `dias_para_pedir`, `menu` y `pedidos_por_recoger`. Para pedir, recargar,
transferir, cancelar, cambiar el permiso de compras sin pedido o bloquear una tarjeta: primero
`preparar_*`, MUESTRA el resumen al papá y llama `confirmar` con el folio SÓLO si el papá dijo que
sí. Nunca inventes ids: usa los que devuelven las consultas. Montos en pesos."""


class ErrorMcp(ToolError):
    """Se devuelve a la IA como error de la herramienta, con su texto (cualquier otra excepción
    llega como «Error executing tool», sin detalles internos)."""


def llave_candado(folio: str) -> int:
    return candado.llave("mcp", folio)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def nuevo_token() -> str:
    return "mcp_" + secrets.token_urlsafe(32)


def _operador(db: Session, ctx: Context) -> Operador:
    req = ctx.request_context.request
    if req is None:
        raise ErrorMcp("Sólo por HTTP.")
    op = db.scalar(select(Operador).where(Operador.host == req.url.hostname))
    auth = req.headers.get("authorization") or ""
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if op is None or not op.mcp_token_hash or not token or not hmac.compare_digest(
            hash_token(token).encode(), op.mcp_token_hash.encode()):
        raise ErrorMcp("No autorizado.")
    return op


def _tutor(db: Session, op: Operador, telefono: str) -> Tutor:
    tutor = bot.tutor_por_telefono(db, op, telefono)
    if tutor is None:
        raise ErrorMcp("Ese número no está registrado en la cafetería.")
    return tutor


def _hijo(app, op, tutor, alumno_id: int) -> dict:
    for h in acciones.hijos(app, op, tutor):
        if h["id"] == alumno_id:
            return h
    raise ErrorMcp("Ese alumno no es hijo de este papá.")


def _ficha(h: dict) -> dict:
    return {"alumno_id": h["id"], "nombre": h.get("nombre"),
            "disponible": str(h.get("disponible")), "apartado": str(h.get("retenido")),
            "compra_sin_pedido": h.get("compra_sin_pedido"),
            "tarjetas": [{"credencial_id": c["id"],
                          "tipo": TIPOS_CREDENCIAL.get(c.get("tipo"), "Credencial"),
                          "bloqueada": c.get("bloqueada")}
                         for c in h.get("credenciales") or [] if isinstance(c, dict)]}


def crear_servidor(app: FastAPI) -> MCPServer:
    srv = MCPServer(name="cafeteria-portal", instructions=INSTRUCCIONES)

    def con_db(fn):
        """Abre la sesión, autentica y traduce los errores de dominio para la IA."""
        def envuelta(ctx: Context, **kw):
            with Session(app.state.engine) as db:
                try:
                    op = _operador(db, ctx)
                    return fn(db, op, **kw)
                except SinTPV:
                    raise ErrorMcp("La cafetería no responde; intenta en un momento.") from None
        return envuelta

    def preparar(db, op, tutor, accion: str, datos: dict, resumen: str) -> dict:
        if not op.bot_escrituras:
            raise ErrorMcp("Por ahora esta acción no está disponible por este canal.")
        folio = uuid.uuid4().hex
        db.add(McpConfirmacion(folio=folio, operador_id=op.id, tutor_id=tutor.id, accion=accion,
                               datos=datos, llave=uuid.uuid4().hex,
                               expira=datetime.now(timezone.utc) + VIGENCIA_FOLIO))
        db.commit()
        return {"folio": folio, "resumen": resumen,
                "instruccion": "Muestra el resumen al papá y llama `confirmar` sólo si dice que sí."}

    # -- consultas ------------------------------------------------------------------------
    @srv.tool(description="Hijos del papá con su saldo disponible, apartado, permiso y tarjetas.")
    def hijos(telefono: str, ctx: Context) -> dict:
        def f(db, op):
            tutor = _tutor(db, op, telefono)
            return {"hijos": [_ficha(h) for h in acciones.hijos(app, op, tutor)]}
        return con_db(f)(ctx)

    @srv.tool(description="Días (AAAA-MM-DD) para los que todavía se puede pedir.")
    def dias_para_pedir(telefono: str, ctx: Context) -> dict:
        def f(db, op):
            _tutor(db, op, telefono)
            return {"dias": [d.isoformat() for d in acciones.dias_para_pedir(db, app, op)]}
        return con_db(f)(ctx)

    @srv.tool(description="Productos que se pueden pedir, con su precio.")
    def menu(telefono: str, ctx: Context) -> dict:
        def f(db, op):
            _tutor(db, op, telefono)
            return {"productos": [{"producto_id": p["id"], "nombre": p["nombre"],
                                   "categoria": p["categoria"], "precio": str(p["precio"])}
                                  for p in acciones.menu(app, op)]}
        return con_db(f)(ctx)

    @srv.tool(description="Pedidos por recoger de un hijo (los que se pueden cancelar).")
    def pedidos_por_recoger(telefono: str, alumno_id: int, ctx: Context) -> dict:
        def f(db, op):
            tutor = _tutor(db, op, telefono)
            _hijo(app, op, tutor, alumno_id)
            return {"pedidos": [{"pedido_id": p["id"], "folio": p["nombre"],
                                 "fecha_entrega": p["fecha_entrega"].isoformat(),
                                 "total": str(p.get("total"))}
                                for p in acciones.pedidos_por_recoger(app, op, tutor, alumno_id)]}
        return con_db(f)(ctx)

    # -- preparar -------------------------------------------------------------------------
    @srv.tool(description="Prepara un pedido. productos: [{producto_id, cantidad}]. fecha: AAAA-MM-DD.")
    def preparar_pedido(telefono: str, alumno_id: int, fecha: str, productos: list[dict],
                        ctx: Context) -> dict:
        def f(db, op):
            tutor = _tutor(db, op, telefono)
            hijo = _hijo(app, op, tutor, alumno_id)
            try:
                dia = date.fromisoformat(fecha)
            except (TypeError, ValueError):
                raise ErrorMcp("Fecha no válida (AAAA-MM-DD).") from None
            motivo = motivo_fecha_invalida(cargar_reglas(db, op.id), dia, acciones.ahora(app))
            if motivo:
                raise ErrorMcp(motivo)
            carta = acciones.menu(app, op)
            pares = [(str(p.get("producto_id")), str(p.get("cantidad")))
                     for p in productos if isinstance(p, dict)]
            items, error = validar_lineas(pares, carta, cero_omite=False)
            if error:
                raise ErrorMcp(error)
            nombres = {p["id"]: (p["nombre"], p["precio"]) for p in carta}
            total = sum(nombres[pid][1] * n for pid, n in items)
            lineas = ", ".join(f"{n} × {nombres[pid][0]}" for pid, n in items)
            return preparar(db, op, tutor, "pedido",
                            {"alumno_id": alumno_id, "fecha": dia.isoformat(),
                             "items": [[pid, n] for pid, n in items], "total": str(total)},
                            f"Pedido de {hijo['nombre']} para el {dia.isoformat()}: {lineas}. "
                            f"Total {dinero(total)}.")
        return con_db(f)(ctx)

    @srv.tool(description="Prepara una recarga con liga de pago de Mercado Pago. "
                          "reparto: [{alumno_id, importe}] en pesos enteros; total de $50 a $2,000.")
    def preparar_recarga(telefono: str, reparto: list[dict], ctx: Context) -> dict:
        def f(db, op):
            tutor = _tutor(db, op, telefono)
            if not op.mp_access_token_cif:
                raise ErrorMcp("Las recargas no están disponibles por ahora.")
            hijos_ = acciones.hijos(app, op, tutor)
            suyos = {h["id"] for h in hijos_}
            datos = {}
            for p in reparto:
                if not isinstance(p, dict) or p.get("alumno_id") not in suyos:
                    raise ErrorMcp("Hay un alumno que no es hijo de este papá.")
                datos[f"importe_{p['alumno_id']}"] = str(p.get("importe", ""))
            armado = recargas.armar_reparto(hijos_, datos)
            if isinstance(armado, str):
                raise ErrorMcp(armado)
            rep, total = armado
            lineas = ", ".join(f"{p['nombre']} {dinero(p['importe'])}" for p in rep)
            return preparar(db, op, tutor, "recarga", {"reparto": rep, "total": str(total)},
                            f"Recarga: {lineas}. Total {dinero(total)}. Se le enviará una liga de "
                            "pago de Mercado Pago.")
        return con_db(f)(ctx)

    @srv.tool(description="Prepara pasar saldo disponible de un hijo a otro (importe en pesos).")
    def preparar_transferencia(telefono: str, origen_id: int, destino_id: int, importe: str,
                               ctx: Context) -> dict:
        def f(db, op):
            tutor = _tutor(db, op, telefono)
            o, d = _hijo(app, op, tutor, origen_id), _hijo(app, op, tutor, destino_id)
            if o["id"] == d["id"]:
                raise ErrorMcp("Elige dos hijos distintos.")
            monto = parse_importe(str(importe))
            if monto is None:
                raise ErrorMcp("Importe no válido.")
            return preparar(db, op, tutor, "transferencia",
                            {"origen": o["id"], "destino": d["id"], "importe": str(monto)},
                            f"Pasar {dinero(monto)} de {o['nombre']} a {d['nombre']}.")
        return con_db(f)(ctx)

    @srv.tool(description="Prepara cancelar un pedido por recoger (el dinero vuelve al disponible).")
    def preparar_cancelacion(telefono: str, alumno_id: int, pedido_id: int, ctx: Context) -> dict:
        def f(db, op):
            tutor = _tutor(db, op, telefono)
            hijo = _hijo(app, op, tutor, alumno_id)
            pedido = next((p for p in acciones.pedidos_por_recoger(app, op, tutor, alumno_id)
                           if p["id"] == pedido_id), None)
            if pedido is None:
                raise ErrorMcp("Ese pedido no está por recoger.")
            motivo = motivo_no_cancelable(cargar_reglas(db, op.id), pedido, acciones.ahora(app))
            if motivo:
                raise ErrorMcp(motivo)
            return preparar(db, op, tutor, "cancelacion",
                            {"alumno_id": alumno_id, "pedido_id": pedido_id},
                            f"Cancelar el pedido {pedido['nombre']} de {hijo['nombre']} "
                            f"({pedido['fecha_entrega'].isoformat()}, {dinero(pedido.get('total'))}).")
        return con_db(f)(ctx)

    @srv.tool(description="Prepara permitir (true) o no (false) que un hijo compre sin pedido con su saldo.")
    def preparar_permiso(telefono: str, alumno_id: int, permitir: bool, ctx: Context) -> dict:
        def f(db, op):
            tutor = _tutor(db, op, telefono)
            hijo = _hijo(app, op, tutor, alumno_id)
            texto = (f"Permitir que {hijo['nombre']} compre sin pedido." if permitir
                     else f"Que {hijo['nombre']} sólo recoja lo que se le pida.")
            return preparar(db, op, tutor, "permiso",
                            {"alumno_id": alumno_id, "permitir": bool(permitir)}, texto)
        return con_db(f)(ctx)

    @srv.tool(description="Prepara bloquear (true) o desbloquear (false) una tarjeta o QR de un hijo.")
    def preparar_tarjeta(telefono: str, alumno_id: int, credencial_id: int, bloquear: bool,
                         ctx: Context) -> dict:
        def f(db, op):
            tutor = _tutor(db, op, telefono)
            hijo = _hijo(app, op, tutor, alumno_id)
            cred = next((c for c in hijo.get("credenciales") or []
                         if isinstance(c, dict) and c.get("id") == credencial_id), None)
            if cred is None:
                raise ErrorMcp("Esa tarjeta no es de este hijo.")
            etiqueta = TIPOS_CREDENCIAL.get(cred.get("tipo"), "Credencial")
            return preparar(db, op, tutor, "tarjeta",
                            {"alumno_id": alumno_id, "credencial_id": credencial_id,
                             "bloquear": bool(bloquear)},
                            f"{'Bloquear' if bloquear else 'Desbloquear'} {etiqueta.lower()} de "
                            f"{hijo['nombre']}.")
        return con_db(f)(ctx)

    # -- confirmar ------------------------------------------------------------------------
    @srv.tool(description="Ejecuta una acción preparada. Sólo después de que el papá dijo que sí.")
    def confirmar(telefono: str, folio: str, ctx: Context) -> dict:
        def f(db, op):
            tutor = _tutor(db, op, telefono)
            existe = db.scalar(select(McpConfirmacion.id).where(
                McpConfirmacion.folio == str(folio), McpConfirmacion.operador_id == op.id,
                McpConfirmacion.tutor_id == tutor.id))
            if existe is None:
                raise ErrorMcp("Folio desconocido.")
            # Una confirmación del folio a la vez, durante TODA la ejecución: las acciones hacen
            # commit a medio camino y un FOR UPDATE se soltaría ahí (Codex).
            k = llave_candado(str(folio))
            engine = db.get_bind()
            db.rollback()
            try:
                with candado.tomar(engine, k, ESPERA_CANDADO):
                    return _confirmar(db, op, tutor, existe)
            except candado.Ocupado:
                raise ErrorMcp("Esa confirmación está en proceso; consulta en un momento.") from None
        return con_db(f)(ctx)

    def _confirmar(db, op, tutor, conf_id: int) -> dict:
        conf = db.get(McpConfirmacion, conf_id)
        if conf is None:
            raise ErrorMcp("Folio desconocido.")
        if conf.estado in ("ok", "rechazo"):  # ya se ejecutó: misma respuesta, sin repetir
            return {"estado": conf.estado, "mensaje": conf.resultado}
        # Sólo vence lo que nunca se intentó. Un incierto NO vence: pudo entrar, y prepararlo otra
        # vez daría otra llave y podría duplicarlo; se reintenta siempre con la suya (Codex).
        if conf.estado == "preparada" and datetime.now(timezone.utc) > conf.expira:
            raise ErrorMcp("El folio venció; prepara la acción otra vez.")
        if not op.bot_escrituras:
            raise ErrorMcp("Por ahora esta acción no está disponible por este canal.")
        r = _ejecutar(db, app, op, tutor, conf)
        conf = db.get(McpConfirmacion, conf_id)  # las acciones hacen commit a medio camino
        if conf.estado not in ("ok", "rechazo"):  # un definitivo nunca se degrada
            conf.estado = r.estado
            conf.resultado = r.texto
            db.commit()
        return {"estado": conf.estado, "mensaje": conf.resultado}

    return srv


def _ejecutar(db, app, op, tutor, conf: McpConfirmacion) -> acciones.Resultado:
    d, llave = conf.datos, conf.llave

    def hijo(i):
        h = next((x for x in acciones.hijos(app, op, tutor) if x["id"] == i), None)
        if h is None:
            raise ErrorMcp("Ese alumno ya no es hijo de este papá.")
        return h

    if conf.accion == "pedido":
        return acciones.pedir(db, app, op, tutor, hijo(d["alumno_id"]),
                              date.fromisoformat(d["fecha"]),
                              [(pid, n) for pid, n in d["items"]], llave, Decimal(d["total"]))
    if conf.accion == "recarga":
        ya = db.scalar(select(Recarga).where(Recarga.operador_id == op.id,
                                             Recarga.tutor_id == tutor.id, Recarga.llave == llave))
        if ya is None or not ya.init_point:
            for p in d["reparto"]:
                hijo(p["alumno_id"])  # sin liga emitida: siguen siendo suyos AHORA (Codex)
        return acciones.recargar(db, app, op, tutor, d["reparto"], Decimal(d["total"]), llave)
    if conf.accion == "transferencia":
        return acciones.transferir(db, app, op, tutor, hijo(d["origen"]), hijo(d["destino"]),
                                   Decimal(d["importe"]), llave)
    if conf.accion == "cancelacion":
        return acciones.cancelar(db, app, op, tutor, hijo(d["alumno_id"]), d["pedido_id"], llave)
    if conf.accion == "permiso":
        return acciones.permiso(db, app, op, tutor, hijo(d["alumno_id"]), d["permitir"], llave)
    if conf.accion == "tarjeta":
        h = hijo(d["alumno_id"])
        cred = next((c for c in h.get("credenciales") or []
                     if isinstance(c, dict) and c.get("id") == d["credencial_id"]), None)
        if cred is None:
            raise ErrorMcp("Esa tarjeta ya no es de este hijo.")
        return acciones.credencial(db, app, op, tutor, h, cred, d["bloquear"], llave)
    raise ErrorMcp("Acción desconocida.")
