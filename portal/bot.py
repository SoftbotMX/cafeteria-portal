"""Bot de WhatsApp guiado: el papá contesta con números y el portal decide el siguiente paso.

Una conversación por (operador, teléfono) en `bot_conversacion.datos`: `paso` y lo elegido. Se
olvida a los 30 minutos sin mensajes. Las acciones son las de `portal.acciones` (las mismas reglas
que la web). Cada confirmación lleva su llave, guardada aquí: un reintento tras un «no sabemos si
entró» usa la MISMA llave y no duplica.
"""
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portal import acciones, candado, recargas
from portal.formato import TIPOS_CREDENCIAL, dinero, parse_importe
from portal.models import BotConversacion, BotMensaje, Operador, Recarga, Tutor
from portal.tutor_tpv import MSG_SIN_MENU, MSG_SIN_PEDIDOS, MSG_SIN_TPV, SinTPV

OLVIDO = timedelta(minutes=30)
ESPERA_CANDADO = 60.0  # s que un mensaje espera a que termine el anterior del mismo papá
MSG_OCUPADO = "Estoy atendiendo tu mensaje anterior. Escríbeme de nuevo en un momento."
MAX_POR_LINEA = 20
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

MENU = [("saldo", "Ver saldo"), ("recargar", "Recargar"), ("pedir", "Pedir"),
        ("pedidos", "Mis pedidos (cancelar)"), ("transferir", "Transferir saldo"),
        ("permiso", "Compras sin pedido"), ("tarjetas", "Tarjetas (bloquear/desbloquear)")]
ESCRITURAS = {"recargar", "pedir", "pedidos", "transferir", "permiso", "tarjetas"}
PIE = "\n\nEscribe «menú» para volver al inicio."
NO_REGISTRADO = ("Este número no está registrado en la cafetería. Pide a la administración que "
                 "lo dé de alta.")


def _normal(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def telefono10(sender: str) -> str | None:
    """Los 10 dígitos de un número MEXICANO (52 o 521 + 10). Otro país o largo: None (no
    registrado). Tomar los últimos 10 de cualquier número dejaría a un extranjero con la misma
    terminación pasar por un papá (revisión de seguridad)."""
    if not isinstance(sender, str) or any(c.isdigit() and not c.isascii() for c in sender):
        return None
    d = re.sub(r"[^0-9]", "", sender)
    if (len(d) == 12 and d.startswith("52")) or (len(d) == 13 and d.startswith("521")):
        return d[-10:]
    return None


def tutor_por_telefono(db: Session, operador: Operador, sender: str) -> Tutor | None:
    t10 = telefono10(sender)
    if not t10:
        return None
    return db.scalar(select(Tutor).where(Tutor.operador_id == operador.id, Tutor.telefono == t10,
                                         Tutor.activo.is_(True)))


def _numero(texto: str, maximo: int) -> int | None:
    t = _normal(texto)
    if t.isdigit() and 1 <= int(t) <= maximo:
        return int(t)
    return None


def _dia(d: date, hoy: date) -> str:
    if d == hoy:
        return "Hoy"
    if d == hoy + timedelta(days=1):
        return "Mañana"
    return f"{DIAS[d.weekday()]} {d.day:02d}/{d.month:02d}"


def _numerada(opciones: list[str]) -> str:
    return "\n".join(f"{i} {o}" for i, o in enumerate(opciones, 1))


class Bot:
    def __init__(self, db: Session, app: FastAPI, operador: Operador, tutor: Tutor,
                 mensaje_id: int | None = None):
        self.db, self.app, self.op, self.tutor = db, app, operador, tutor
        self.mensaje_id = mensaje_id

    # -- conversación ---------------------------------------------------------------------
    def _conversacion(self) -> BotConversacion:
        q = (select(BotConversacion).where(BotConversacion.operador_id == self.op.id,
                                           BotConversacion.telefono == self.tutor.telefono)
             .with_for_update())
        c = self.db.scalar(q)
        if c is None:
            try:
                with self.db.begin_nested():
                    c = BotConversacion(operador_id=self.op.id, telefono=self.tutor.telefono,
                                        datos={"paso": "menu"})
                    self.db.add(c)
            except IntegrityError:  # dos mensajes a la vez: la otra petición la creó
                c = self.db.scalar(q)
        return c

    def atender(self, texto: str) -> str:
        c = self._conversacion()
        datos = dict(c.datos or {})
        viejo = c.actualizada and datetime.now(timezone.utc) - c.actualizada > OLVIDO
        t = _normal(texto)
        if viejo or t in ("menu", "0", "inicio", "hola", "cancelar") or not datos.get("paso"):
            datos = {"paso": "menu"}
            respuesta = self._menu(saludo=True)
        else:
            try:
                datos, respuesta = self._paso(datos, texto)
            except SinTPV:
                respuesta = MSG_SIN_TPV + PIE
        c.datos = datos
        c.actualizada = datetime.now(timezone.utc)
        if self.mensaje_id is not None:
            # El paso nuevo y la respuesta del mensaje en la MISMA transacción: si el proceso
            # muriera entre los dos, reprocesar el mensaje lo leería contra el paso siguiente
            # (un «1» que elegía tarjeta confirmaría el bloqueo). Codex.
            m = self.db.get(BotMensaje, self.mensaje_id)
            if m is not None:
                m.respuesta = respuesta
        self.db.commit()
        return respuesta

    def _menu(self, saludo=False, antes: str = "") -> str:
        nombre = (self.tutor.nombre or "").split()[0] if self.tutor.nombre else ""
        cabeza = f"Hola {nombre}. " if saludo and nombre else ""
        return (f"{antes}{cabeza}¿Qué deseas hacer?\n" + _numerada([e for _, e in MENU])
                + "\n\nEscribe el número.")

    def _al_menu(self, texto: str) -> tuple[dict, str]:
        return {"paso": "menu"}, self._menu(antes=texto + "\n\n")

    # -- despacho -------------------------------------------------------------------------
    def _paso(self, datos: dict, texto: str) -> tuple[dict, str]:
        paso = datos["paso"]
        manejador = getattr(self, f"_p_{paso}", None)
        if manejador is None:
            return {"paso": "menu"}, self._menu()
        return manejador(datos, texto)

    def _p_menu(self, datos, texto):
        n = _numero(texto, len(MENU))
        if n is None:
            return datos, self._menu(antes="No entendí. ")
        accion = MENU[n - 1][0]
        if accion in ESCRITURAS and not self.op.bot_escrituras:
            return self._al_menu(f"Por ahora por WhatsApp sólo puedes ver el saldo. Para eso entra "
                                 f"a https://{self.op.host}")
        if accion == "saldo":
            return self._al_menu(self._saldos())
        if accion == "recargar" and not self.op.mp_access_token_cif:
            return self._al_menu("Las recargas no están disponibles por ahora.")
        hijos = acciones.hijos(self.app, self.op, self.tutor)
        if not hijos:
            return self._al_menu("No tienes hijos vinculados. Pide a la cafetería que los dé de alta.")
        if accion == "transferir" and len(hijos) < 2:
            return self._al_menu("Para transferir necesitas dos hijos o más.")
        return self._elegir_hijo({"accion": accion}, hijos)

    # -- elegir hijo (común) --------------------------------------------------------------
    def _elegir_hijo(self, datos: dict, hijos: list[dict], excluir: int | None = None,
                     pregunta: str = "¿De quién?") -> tuple[dict, str]:
        opciones = [h for h in hijos if h["id"] != excluir]
        if len(opciones) == 1 and datos["accion"] != "transferir":
            return self._con_hijo(datos, opciones[0])
        datos = {**datos, "paso": "hijo", "opciones": [h["id"] for h in opciones]}
        extra = ["Todos por igual"] if datos["accion"] == "recargar" and len(opciones) > 1 else []
        lineas = [f"{h['nombre']} ({dinero(h.get('disponible'))} disponible)" for h in opciones]
        return datos, f"{pregunta}\n" + _numerada(lineas + extra) + PIE

    def _p_hijo(self, datos, texto):
        ids = datos["opciones"]
        todos = datos["accion"] == "recargar" and len(ids) > 1
        n = _numero(texto, len(ids) + (1 if todos else 0))
        if n is None:
            return datos, "No entendí: escribe el número de la lista." + PIE
        hijos = {h["id"]: h for h in acciones.hijos(self.app, self.op, self.tutor)}
        if todos and n == len(ids) + 1:
            return {**datos, "paso": "rec_monto", "hijos": ids}, (
                f"¿Cuánto a cada uno? Pesos enteros; el total va de ${recargas.MINIMO} a "
                f"${recargas.MAXIMO:,}." + PIE)
        hijo = hijos.get(ids[n - 1])
        if hijo is None:
            return self._al_menu("Ese hijo ya no aparece en tu cuenta.")
        return self._con_hijo(datos, hijo, hijos)

    def _con_hijo(self, datos, hijo, hijos=None):
        accion = datos["accion"]
        if accion == "transferir":
            hijos = hijos or {h["id"]: h for h in acciones.hijos(self.app, self.op, self.tutor)}
            return self._elegir_hijo({"accion": "transferir_destino", "origen": hijo["id"]},
                                     list(hijos.values()), excluir=hijo["id"],
                                     pregunta=f"¿A quién le pasas saldo de {hijo['nombre']}?")
        if accion == "transferir_destino":
            hijos = hijos or {h["id"]: h for h in acciones.hijos(self.app, self.op, self.tutor)}
            origen = hijos.get(datos["origen"])
            if origen is None:
                return self._al_menu("Ese hijo ya no aparece en tu cuenta.")
            return {"accion": accion, "paso": "tra_monto", "origen": origen["id"],
                    "alumno_id": hijo["id"]}, (
                f"¿Cuánto le pasas de {origen['nombre']} a {hijo['nombre']}? Disponible de "
                f"{origen['nombre']}: {dinero(origen.get('disponible'))}." + PIE)
        datos = {"accion": accion, "alumno_id": hijo["id"]}
        if accion == "recargar":
            return {**datos, "paso": "rec_monto", "hijos": [hijo["id"]]}, (
                f"¿Cuánto le recargas a {hijo['nombre']}? Pesos enteros, de ${recargas.MINIMO} a "
                f"${recargas.MAXIMO:,}." + PIE)
        if accion == "pedir":
            return self._pedir_dia(datos, hijo)
        if accion == "pedidos":
            return self._pedidos(datos, hijo)
        if accion == "permiso":
            return self._permiso(datos, hijo)
        if accion == "tarjetas":
            return self._tarjetas(datos, hijo)
        return {"paso": "menu"}, self._menu()

    # -- recargar -------------------------------------------------------------------------
    def _p_rec_monto(self, datos, texto):
        t = _normal(texto).replace("$", "").replace(",", "")
        if not t.isdigit() or int(t) <= 0:
            return datos, "Escribe sólo el número de pesos, sin centavos (por ejemplo 200)." + PIE
        hijos = {h["id"]: h for h in acciones.hijos(self.app, self.op, self.tutor)}
        elegidos = [hijos[i] for i in datos["hijos"] if i in hijos]
        armado = recargas.armar_reparto(elegidos, {f"importe_{h['id']}": t for h in elegidos})
        if isinstance(armado, str):
            return datos, armado + PIE
        reparto, total = armado
        lineas = "\n".join(f"{p['nombre']}: {dinero(p['importe'])}" for p in reparto)
        return {**datos, "paso": "rec_confirmar", "reparto": reparto, "total": str(total),
                "llave": uuid.uuid4().hex}, (
            f"Recarga:\n{lineas}\nTotal: {dinero(total)}\n\n1 Confirmar y recibir la liga de pago"
            f"\n2 Cancelar")

    def _p_rec_confirmar(self, datos, texto):
        def hacer():
            ya = self.db.scalar(select(Recarga).where(
                Recarga.operador_id == self.op.id, Recarga.tutor_id == self.tutor.id,
                Recarga.llave == datos["llave"]))
            if ya is None or not ya.init_point:
                # Sin liga emitida todavía (nueva, o MP falló antes): los hijos tienen que seguir
                # siendo suyos AHORA (Codex): si no, MP cobraría y el TPV rechazaría la acreditación.
                actuales = {h["id"] for h in acciones.hijos(self.app, self.op, self.tutor)}
                if any(p["alumno_id"] not in actuales for p in datos["reparto"]):
                    return acciones.Resultado(
                        "rechazo", "Uno de los hijos ya no aparece en tu cuenta. Arma la recarga "
                                   "otra vez.")
            return acciones.recargar(self.db, self.app, self.op, self.tutor, datos["reparto"],
                                     Decimal(datos["total"]), datos["llave"])
        return self._confirmar(datos, texto, hacer)

    # -- pedir ----------------------------------------------------------------------------
    def _pedir_dia(self, datos, hijo):
        dias = acciones.dias_para_pedir(self.db, self.app, self.op)
        if not dias:
            return self._al_menu("Por ahora no hay días disponibles para pedir.")
        hoy = acciones.ahora(self.app).astimezone(
            acciones.cargar_reglas(self.db, self.op.id).zona).date()
        dias = dias[:10]
        return {**datos, "paso": "ped_dia", "dias": [d.isoformat() for d in dias]}, (
            f"¿Para qué día es el pedido de {hijo['nombre']}?\n"
            + _numerada([_dia(d, hoy) for d in dias]) + PIE)

    def _p_ped_dia(self, datos, texto):
        n = _numero(texto, len(datos["dias"]))
        if n is None:
            return datos, "No entendí: escribe el número del día." + PIE
        try:
            carta = acciones.menu(self.app, self.op)
        except SinTPV:
            return datos, MSG_SIN_MENU + PIE
        if not carta:
            return self._al_menu("El menú está vacío por ahora.")
        carta = carta[:30]
        return {**datos, "paso": "ped_productos", "dia": datos["dias"][n - 1],
                "productos": [[p["id"], p["nombre"], str(p["precio"])] for p in carta],
                "carrito": {}}, self._carta(carta)

    def _carta(self, carta) -> str:
        lineas = [f"{p['nombre']} {dinero(p['precio'])}" for p in carta]
        return ("Menú:\n" + _numerada(lineas) + "\n\nEscribe el número del producto y la cantidad, "
                "por ejemplo «1 2» (dos del producto 1). Un producto por mensaje. Escribe «listo» "
                "al terminar o «quitar 1» para quitar el producto 1." + PIE)

    def _resumen_carrito(self, datos) -> tuple[str, Decimal]:
        nombres = {str(i): (n, Decimal(p)) for i, n, p in datos["productos"]}
        total = Decimal(0)
        lineas = []
        for pid, cant in datos["carrito"].items():
            nombre, precio = nombres[pid]
            total += precio * cant
            lineas.append(f"{cant} × {nombre}")
        return "\n".join(lineas), total

    def _p_ped_productos(self, datos, texto):
        t = _normal(texto)
        productos = datos["productos"]
        carrito = dict(datos["carrito"])
        if t == "listo":
            if not carrito:
                return datos, "Todavía no agregas nada. Escribe por ejemplo «1 2»." + PIE
            resumen, total = self._resumen_carrito(datos)
            dia = date.fromisoformat(datos["dia"])
            return {**datos, "paso": "ped_confirmar", "llave": uuid.uuid4().hex,
                    "total": str(total)}, (
                f"Pedido para el {DIAS[dia.weekday()]} {dia.day:02d}/{dia.month:02d}:\n{resumen}\n"
                f"Total: {dinero(total)}\n\n1 Confirmar\n2 Cancelar")
        m = re.fullmatch(r"quitar\s+(\d+)", t)
        if m:
            i = int(m.group(1))
            if 1 <= i <= len(productos):
                carrito.pop(str(productos[i - 1][0]), None)
            datos = {**datos, "carrito": carrito}
            return datos, self._eco(datos)
        m = re.fullmatch(r"(\d+)(?:\s*(?:x|\s)\s*(\d+))?", t)
        if not m or not 1 <= int(m.group(1)) <= len(productos):
            return datos, "No entendí. Escribe el número del producto y la cantidad, por ejemplo «1 2»." + PIE
        cant = int(m.group(2) or 1)
        if not 1 <= cant <= MAX_POR_LINEA:
            return datos, f"La cantidad va de 1 a {MAX_POR_LINEA}." + PIE
        carrito[str(productos[int(m.group(1)) - 1][0])] = cant
        datos = {**datos, "carrito": carrito}
        return datos, self._eco(datos)

    def _eco(self, datos) -> str:
        if not datos["carrito"]:
            return "Tu pedido está vacío. Escribe por ejemplo «1 2»." + PIE
        resumen, total = self._resumen_carrito(datos)
        return f"Llevas:\n{resumen}\nTotal: {dinero(total)}\n\nAgrega otro o escribe «listo»." + PIE

    def _p_ped_confirmar(self, datos, texto):
        def hacer():
            hijo = self._hijo(datos["alumno_id"])
            if hijo is None:
                return acciones.Resultado("rechazo", "Ese hijo ya no aparece en tu cuenta.")
            items = [(int(pid), n) for pid, n in datos["carrito"].items()]
            return acciones.pedir(self.db, self.app, self.op, self.tutor, hijo,
                                  date.fromisoformat(datos["dia"]), items, datos["llave"],
                                  # conversaciones guardadas antes de este campo: sin comparar
                                  Decimal(datos["total"]) if datos.get("total") else None)
        return self._confirmar(datos, texto, hacer)

    # -- mis pedidos (cancelar) -----------------------------------------------------------
    def _pedidos(self, datos, hijo):
        try:
            lista = acciones.pedidos_por_recoger(self.app, self.op, self.tutor, hijo["id"])
        except SinTPV:
            return self._al_menu(MSG_SIN_PEDIDOS)
        if not lista:
            return self._al_menu(f"{hijo['nombre']} no tiene pedidos por recoger.")
        lista = lista[:15]
        lineas = [f"{p['nombre']} · {DIAS[p['fecha_entrega'].weekday()]} "
                  f"{p['fecha_entrega'].day:02d}/{p['fecha_entrega'].month:02d} · "
                  f"{dinero(p.get('total'))}" for p in lista]
        return {**datos, "paso": "can_pedido", "pedidos": [[p["id"], p["nombre"]] for p in lista]}, (
            f"Pedidos por recoger de {hijo['nombre']}. ¿Cuál quieres cancelar?\n"
            + _numerada(lineas) + PIE)

    def _p_can_pedido(self, datos, texto):
        n = _numero(texto, len(datos["pedidos"]))
        if n is None:
            return datos, "No entendí: escribe el número del pedido." + PIE
        pid, folio = datos["pedidos"][n - 1]
        return {**datos, "paso": "can_confirmar", "pedido_id": pid, "folio": folio,
                "llave": uuid.uuid4().hex}, (
            f"¿Cancelar el pedido {folio}? El dinero vuelve al disponible.\n\n1 Sí, cancelar\n2 No")

    def _p_can_confirmar(self, datos, texto):
        def hacer():
            hijo = self._hijo(datos["alumno_id"])
            if hijo is None:
                return acciones.Resultado("rechazo", "Ese hijo ya no aparece en tu cuenta.")
            return acciones.cancelar(self.db, self.app, self.op, self.tutor, hijo,
                                     datos["pedido_id"], datos["llave"])
        return self._confirmar(datos, texto, hacer)

    # -- transferir -----------------------------------------------------------------------
    def _p_tra_monto(self, datos, texto):
        importe = parse_importe(texto.replace("$", "").replace(",", "").strip())
        if importe is None:
            return datos, "Escribe el importe, por ejemplo 150 o 150.50." + PIE
        hijos = {h["id"]: h for h in acciones.hijos(self.app, self.op, self.tutor)}
        o, d = hijos.get(datos["origen"]), hijos.get(datos["alumno_id"])
        if not o or not d:
            return self._al_menu("Uno de tus hijos ya no aparece en tu cuenta.")
        return {**datos, "paso": "tra_confirmar", "importe": str(importe),
                "llave": uuid.uuid4().hex}, (
            f"Pasar {dinero(importe)} de {o['nombre']} a {d['nombre']}. No se puede deshacer "
            f"desde aquí.\n\n1 Confirmar\n2 Cancelar")

    def _p_tra_confirmar(self, datos, texto):
        def hacer():
            hijos = {h["id"]: h for h in acciones.hijos(self.app, self.op, self.tutor)}
            o, d = hijos.get(datos["origen"]), hijos.get(datos["alumno_id"])
            if not o or not d:
                return acciones.Resultado("rechazo", "Uno de tus hijos ya no aparece en tu cuenta.")
            return acciones.transferir(self.db, self.app, self.op, self.tutor, o, d,
                                       Decimal(datos["importe"]), datos["llave"])
        return self._confirmar(datos, texto, hacer)

    # -- compras sin pedido ---------------------------------------------------------------
    def _permiso(self, datos, hijo):
        actual = hijo.get("compra_sin_pedido")
        if actual is True:
            estado, nuevo, accion = (f"{hijo['nombre']} puede comprar sin pedido con su saldo.",
                                     False, "Que sólo recoja lo que pidas")
        else:
            estado, nuevo, accion = (f"{hijo['nombre']} sólo puede recoger lo que pidas.", True,
                                     "Permitir que compre sin pedido")
        return {**datos, "paso": "per_confirmar", "permitir": nuevo, "llave": uuid.uuid4().hex}, (
            f"{estado}\n\n1 {accion}\n2 Dejarlo así")

    def _p_per_confirmar(self, datos, texto):
        def hacer():
            hijo = self._hijo(datos["alumno_id"])
            if hijo is None:
                return acciones.Resultado("rechazo", "Ese hijo ya no aparece en tu cuenta.")
            return acciones.permiso(self.db, self.app, self.op, self.tutor, hijo,
                                    datos["permitir"], datos["llave"])
        return self._confirmar(datos, texto, hacer)

    # -- tarjetas -------------------------------------------------------------------------
    def _tarjetas(self, datos, hijo):
        creds = [x for x in hijo.get("credenciales") or [] if isinstance(x, dict)
                 and isinstance(x.get("id"), int) and isinstance(x.get("bloqueada"), bool)]
        if not creds:
            return self._al_menu(f"{hijo['nombre']} no tiene tarjeta ni código QR.")
        lineas = [f"{TIPOS_CREDENCIAL.get(x.get('tipo'), 'Credencial')} · "
                  f"{'Bloqueada' if x['bloqueada'] else 'Activa'}" for x in creds]
        return {**datos, "paso": "tar_cred", "creds": [x["id"] for x in creds]}, (
            f"Tarjetas de {hijo['nombre']}. ¿Cuál?\n" + _numerada(lineas) + PIE)

    def _p_tar_cred(self, datos, texto):
        n = _numero(texto, len(datos["creds"]))
        if n is None:
            return datos, "No entendí: escribe el número." + PIE
        hijo = self._hijo(datos["alumno_id"])
        cred = next((x for x in (hijo or {}).get("credenciales") or []
                     if isinstance(x, dict) and x.get("id") == datos["creds"][n - 1]), None)
        if cred is None or not isinstance(cred.get("bloqueada"), bool):
            return self._al_menu("Esa tarjeta ya no aparece.")
        bloquear = not cred["bloqueada"]
        etiqueta = TIPOS_CREDENCIAL.get(cred.get("tipo"), "Credencial")
        return {**datos, "paso": "tar_confirmar", "cred_id": cred["id"], "bloquear": bloquear,
                "llave": uuid.uuid4().hex}, (
            f"¿{'Bloquear' if bloquear else 'Desbloquear'} {etiqueta.lower()} de {hijo['nombre']}?"
            + (" Nadie podrá pagar con ella hasta que la desbloquees." if bloquear else "")
            + f"\n\n1 Sí, {'bloquear' if bloquear else 'desbloquear'}\n2 No")

    def _p_tar_confirmar(self, datos, texto):
        def hacer():
            hijo = self._hijo(datos["alumno_id"])
            cred = next((x for x in (hijo or {}).get("credenciales") or []
                         if isinstance(x, dict) and x.get("id") == datos["cred_id"]), None)
            if hijo is None or cred is None:
                return acciones.Resultado("rechazo", "Esa tarjeta ya no aparece.")
            return acciones.credencial(self.db, self.app, self.op, self.tutor, hijo, cred,
                                       datos["bloquear"], datos["llave"])
        return self._confirmar(datos, texto, hacer)

    # -- común ----------------------------------------------------------------------------
    def _hijo(self, alumno_id: int) -> dict | None:
        return next((h for h in acciones.hijos(self.app, self.op, self.tutor)
                     if h["id"] == alumno_id), None)

    def _confirmar(self, datos, texto, hacer) -> tuple[dict, str]:
        n = _numero(texto, 2)
        if n == 2:
            return self._al_menu("Listo, no se hizo nada.")
        if n != 1:
            return datos, "Responde 1 para confirmar o 2 para cancelar."
        if not self.op.bot_escrituras:
            return self._al_menu("Por ahora esta acción no está disponible por WhatsApp.")
        r = hacer()
        if r.estado == "incierto":
            return datos, r.texto  # misma llave: el reintento no duplica
        return self._al_menu(r.texto)

    def _saldos(self) -> str:
        hijos = acciones.hijos(self.app, self.op, self.tutor)
        if not hijos:
            return "No tienes hijos vinculados."
        return "\n".join(f"{h['nombre']}: {dinero(h.get('disponible'))} disponible"
                         + (f" ({dinero(h.get('retenido'))} apartado)" if h.get("retenido") else "")
                         for h in hijos)


def llave_candado(operador_id: int, telefono: str) -> int:
    """Llave del candado de PostgreSQL por papá (bigint con signo)."""
    return candado.llave("bot", operador_id, telefono)


def atender(db: Session, app: FastAPI, operador: Operador, sender: str, texto: str,
            mensaje_id: int | None = None) -> str:
    """La respuesta para lo que escribió `sender`. Un mensaje del mismo papá a la vez: el candado
    vive en una conexión APARTE y dura todo el mensaje — las acciones hacen commit a medio camino
    y un `FOR UPDATE` se soltaría antes de guardar el paso (Codex)."""
    tutor = tutor_por_telefono(db, operador, sender)
    if tutor is None:
        return NO_REGISTRADO
    # Todo lo que se necesita ANTES de soltar la conexión: `rollback` caduca los objetos y leerlos
    # en la espera volvería a tomar una conexión del pool (Codex).
    k = llave_candado(operador.id, tutor.telefono)
    engine = db.get_bind()
    db.rollback()  # suelta la conexión de trabajo mientras se espera el candado
    try:
        with candado.tomar(engine, k, ESPERA_CANDADO):
            return Bot(db, app, operador, tutor, mensaje_id).atender(texto or "")
    except candado.Ocupado:
        return MSG_OCUPADO
