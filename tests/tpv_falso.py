"""TPV simulado: verifica la firma como el real (una sola vez por firma) y guarda por llave."""
import copy
from collections import defaultdict
import hashlib
import hmac
import json
import time
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import httpx

PREFIJO = "/sb_cafe/api/v1/"


def _firma(secreto: str, ts: str, ruta: str, cuerpo: bytes) -> str:
    return "hmac-sha256=" + hmac.new(
        secreto.encode(), f"{ts}.{ruta}.".encode() + cuerpo, hashlib.sha256).hexdigest()


def _err(codigo: str, mensaje: str, status: int) -> httpx.Response:
    return httpx.Response(status, json={"ok": False, "error": {"codigo": codigo, "mensaje": mensaje}})


class TPVFalso:
    def __init__(self, secreto_admin: str = "admin-s", secreto_tutor: str = "tutor-s"):
        self.secreto_admin = secreto_admin
        self.secreto_tutor = secreto_tutor
        self.llamadas: dict[str, int] = {}
        self.fallar_en: set[str] = set()
        self.firmas_vistas: set[str] = set()
        self.guardado: dict[str, tuple[str, dict, dict]] = {}  # llave -> (operacion, cuerpo, respuesta)
        self.tutores: dict[str, dict] = {}
        self.alumnos: dict[int, dict] = {}
        self.movimientos: dict[int, list[dict]] = defaultdict(list)  # más reciente primero, como el real
        self.productos: list[dict] = []  # {id, nombre, categoria, precio}: lo que devuelve `menu`
        self.pedidos: list[dict] = []
        self.cuerpos_precompra: list[dict] = []  # lo que el portal mandó, tal cual
        self.precompras: dict[tuple, tuple] = {}  # (tutor_ref, llave) -> (contenido, pedido)
        self.cancelaciones: dict[tuple, tuple] = {}  # (tutor_ref, llave) -> (huella, respuesta)
        self.cuerpos_cancelar: list[dict] = []  # lo que el portal mandó a precompra/cancelar
        self.una_vez: dict[tuple, tuple] = {}  # (tutor_ref, llave) -> (huella, respuesta): _sb_una_vez
        self.cuerpos_permiso: list[dict] = []  # lo que el portal mandó a alumno/permiso
        self.cuerpos_credencial: list[tuple[str, dict]] = []  # (operación, cuerpo) de credencial/*
        self.cuerpos_transferencia: list[dict] = []  # lo que el portal mandó a transferencia
        self.transferencias: dict[tuple, tuple] = {}  # (tutor_ref, llave) -> (origen, destino, importe)
        self.hoy: date | None = None  # fija el «hoy» del TPV; por omisión, el de México
        self.recargas: dict[str, str] = {}  # pago_ref -> huella (op_recarga)
        self.cuerpos_recarga: list[dict] = []  # lo que el portal mandó a recarga
        self.transport = httpx.MockTransport(self._manejar)

    def _manejar(self, req: httpx.Request) -> httpx.Response:
        ruta = req.url.path
        if not ruta.startswith(PREFIJO):
            return _err("no_encontrada", "Ruta desconocida.", 404)
        op = ruta[len(PREFIJO):]
        secreto = self.secreto_admin if op.startswith("admin/") else self.secreto_tutor
        cuerpo = req.content
        ts = req.headers.get("X-SB-Cafe-Timestamp", "")
        firma = req.headers.get("X-SB-Cafe-Signature", "")
        try:
            vieja = abs(time.time() - int(ts)) > 300
        except ValueError:
            vieja = True
        if vieja or not hmac.compare_digest(firma, _firma(secreto, ts, ruta, cuerpo)):
            return _err("firma_invalida", "Firma inválida.", 401)
        if firma in self.firmas_vistas:
            return _err("repetida", "Esa petición ya se procesó.", 409)
        self.firmas_vistas.add(firma)
        self.llamadas[op] = self.llamadas.get(op, 0) + 1
        if op in self.fallar_en:
            return _err("no_disponible", "Servicio no disponible.", 503)
        try:
            datos = json.loads(cuerpo or b"{}")
        except ValueError:
            return _err("json_invalido", "El cuerpo no es JSON.", 400)
        return self._operar(op, datos)

    def agregar_tutor(self, tutor_ref) -> dict:
        return self.tutores.setdefault(tutor_ref, {
            "tutor_ref": tutor_ref, "nombre": tutor_ref, "telefono": None, "correo": None,
            "activo": True, "alumnos": []})

    def agregar_hijo(self, tutor_ref, nombre, disponible=0, retenido=0, credenciales=None) -> dict:
        t = self.agregar_tutor(tutor_ref)
        aid = len(self.alumnos) + 1
        al = {"id": aid, "nombre": nombre, "matricula": None, "codigo": f"C{aid:04d}",
              "compra_sin_pedido": True, "disponible": disponible, "retenido": retenido,
              "credenciales": credenciales or []}
        self.alumnos[aid] = al
        self.movimientos[aid] = []
        t["alumnos"].append(al)
        return al

    @staticmethod
    def _ficha(al: dict) -> dict:
        """Como `_ficha` del TPV real: sólo las credenciales ACTIVAS (clave `activa` opcional en
        el fake, ausente = activa) y de ellas sólo id, tipo y bloqueada."""
        f = copy.deepcopy(al)
        f["credenciales"] = [{"id": c["id"], "tipo": c["tipo"], "bloqueada": bool(c.get("bloqueada"))}
                             for c in al["credenciales"] if c.get("activa", True)]
        return f

    def _una_vez(self, op: str, d: dict, autorizar, ejecutar) -> httpx.Response:
        """Imita _sb_una_vez del TPV real (alumno/permiso y credencial/*): llave obligatoria; la
        autorización se revisa SIEMPRE, también al repetir; repetir devuelve la respuesta GUARDADA
        sin ejecutar; la misma llave con otro contenido (u otra operación: la llave guardada es de
        todas) es 409 `llave_reutilizada`. Los errores de `ejecutar` no se guardan."""
        t = self.tutores.get(d.get("tutor_ref"))
        if not t or not t["activo"]:
            return _err("tutor_desconocido", "Tutor desconocido.", 404)
        llave = (d.get("llave") or "").strip() if isinstance(d.get("llave"), str) else ""
        if not llave or len(llave) > 120:
            return _err("falta_llave", "Toda escritura lleva su llave de idempotencia.", 400)
        error = autorizar(t)
        if error is not None:
            return error
        huella = json.dumps([op, d], sort_keys=True, default=str)
        previa = self.una_vez.get((t["tutor_ref"], llave))
        if previa:
            if previa[0] != huella:
                return _err("llave_reutilizada", "Esa llave ya se usó para otra operación.", 409)
            return httpx.Response(200, json=copy.deepcopy(previa[1]))
        resultado = ejecutar(t)
        if isinstance(resultado, httpx.Response):
            return resultado
        resp = {"ok": True, **resultado}
        self.una_vez[(t["tutor_ref"], llave)] = (huella, copy.deepcopy(resp))
        return httpx.Response(200, json=resp)

    def _permiso(self, d: dict) -> httpx.Response:
        """Como op_alumno_permiso: alumno ajeno = desconocido; `compra_sin_pedido` ha de ser un
        booleano estricto (nada de "no", 0 ni "false"), si no `dato_invalido`."""
        def autorizar(t):
            if d.get("alumno_id") not in [a["id"] for a in t["alumnos"]]:
                return _err("alumno_desconocido", "Alumno desconocido.", 404)

        def ejecutar(t):
            valor = d.get("compra_sin_pedido")
            if not isinstance(valor, bool):
                return _err("dato_invalido", "compra_sin_pedido tiene que ser true o false.", 400)
            al = self.alumnos[d["alumno_id"]]
            al["compra_sin_pedido"] = valor
            return {"alumno": self._ficha(al)}

        self.cuerpos_permiso.append(copy.deepcopy(d))
        return self._una_vez("alumno_permiso", d, autorizar, ejecutar)

    def _credencial(self, op: str, d: dict) -> httpx.Response:
        """Como op_credencial_bloquear/desbloquear: la credencial ha de ser de un alumno de este
        tutor (aunque esté inactiva y no salga en la ficha), si no 404 `credencial_desconocida`.
        Bloquear lo ya bloqueado no cambia nada y es OK."""
        def buscar(t):
            for a in t["alumnos"]:
                for c in a["credenciales"]:
                    if isinstance(d.get("credencial_id"), int) and c["id"] == d["credencial_id"]:
                        return a, c
            return None

        def autorizar(t):
            if buscar(t) is None:
                return _err("credencial_desconocida", "Credencial desconocida.", 404)

        def ejecutar(t):
            alumno, cred = buscar(t)
            cred["bloqueada"] = op == "credencial/bloquear"
            return {"alumno": self._ficha(alumno)}

        self.cuerpos_credencial.append((op, copy.deepcopy(d)))
        return self._una_vez(op.replace("/", "_"), d, autorizar, ejecutar)

    @staticmethod
    def _importe(valor):
        """Como `_importe` del TPV real: float > 0 redondeado a centavos, si no `importe_invalido`."""
        try:
            importe = round(float(valor), 2)
        except (TypeError, ValueError):
            return None
        return importe if importe > 0 else None

    def _recarga(self, d: dict) -> httpx.Response:
        """Como op_recarga: idempotente por `pago_ref`; el mismo pago con OTRO reparto es
        409 `pago_ya_acreditado`; el reparto tiene que sumar el total y ser de SUS alumnos."""
        self.cuerpos_recarga.append(copy.deepcopy(d))
        t = self.tutores.get(d.get("tutor_ref"))
        if not t or not t["activo"]:
            return _err("tutor_desconocido", "Tutor desconocido.", 404)
        pago_ref = (d.get("pago_ref") or "").strip() if isinstance(d.get("pago_ref"), str) else ""
        if not pago_ref or len(pago_ref) > 120:
            return _err("falta_pago", "Falta la referencia del pago.", 400)
        total = self._importe(d.get("total"))
        if total is None:
            return _err("importe_invalido", "Importe no válido.", 400)
        reparto = []
        for parte in d.get("reparto") or []:
            if not isinstance(parte, dict):
                return _err("dato_invalido", "Reparto no válido.", 400)
            al = next((a for a in t["alumnos"] if a["id"] == parte.get("alumno_id")), None)
            if al is None:
                return _err("alumno_desconocido", "Alumno desconocido.", 404)
            importe = self._importe(parte.get("importe"))
            if importe is None:
                return _err("importe_invalido", "Importe no válido.", 400)
            reparto.append((al, importe))
        if not reparto or len({a["id"] for a, _i in reparto}) != len(reparto):
            return _err("reparto_invalido", "El reparto necesita al menos un alumno y sin repetir.", 400)
        if round(sum(i for _a, i in reparto), 2) != total:
            return _err("reparto_invalido", "El reparto no suma el total del pago.", 400)
        huella = json.dumps([t["tutor_ref"], total] + sorted([a["id"], i] for a, i in reparto))
        previa = self.recargas.get(pago_ref)
        if previa is not None and previa != huella:
            return _err("pago_ya_acreditado", "Ese pago ya se acreditó con otro reparto.", 409)
        if previa is None:
            self.recargas[pago_ref] = huella
            for al, importe in reparto:
                al["disponible"] = round(al["disponible"] + importe, 2)
                self.movimientos[al["id"]].insert(0, {
                    "fecha": "2026-10-22 12:00:00", "tipo": "recarga", "descripcion": "Recarga",
                    "disponible": importe, "retenido": 0, "venta": None})
        return httpx.Response(200, json={"ok": True, "pago_ref": pago_ref,
                                         "alumnos": [self._ficha(a) for a, _i in reparto]})

    def _lectura_tutor(self, op: str, d: dict) -> httpx.Response | None:
        t = self.tutores.get(d.get("tutor_ref"))
        if not t or not t["activo"]:
            return _err("tutor_desconocido", "Tutor desconocido.", 404)
        if op == "tutor/alumnos":
            return httpx.Response(200, json={"ok": True,
                                             "alumnos": [self._ficha(a) for a in t["alumnos"]]})
        # Un alumno ajeno responde igual que uno inexistente.
        if d.get("alumno_id") not in [a["id"] for a in t["alumnos"]]:
            return _err("alumno_desconocido", "Alumno desconocido.", 404)
        try:
            pagina = max(int(d.get("pagina") or 1), 1)
            por = min(max(int(d.get("por_pagina") or 50), 1), 100)
        except (TypeError, ValueError):
            return _err("dato_invalido", "Página no válida.", 400)
        movs = self.movimientos.get(d["alumno_id"], [])
        return httpx.Response(200, json={
            "ok": True, "total": len(movs), "pagina": pagina,
            "movimientos": copy.deepcopy(movs[(pagina - 1) * por: pagina * por])})

    def _precompra(self, d: dict) -> httpx.Response:
        """Imita op_precompra + _sb_crear del TPV real: los rechazos del dominio (saldo, fecha,
        cantidad, contenido distinto con la misma llave) son 409 `rechazado`, no códigos propios."""
        t = self.tutores.get(d.get("tutor_ref"))
        if not t or not t["activo"]:
            return _err("tutor_desconocido", "Tutor desconocido.", 404)
        al = next((a for a in t["alumnos"] if a["id"] == d.get("alumno_id")), None)
        if al is None:
            return _err("alumno_desconocido", "Alumno desconocido.", 404)
        llave = (d.get("llave") or "").strip() if isinstance(d.get("llave"), str) else ""
        if not llave or len(llave) > 120:
            return _err("falta_llave", "Toda escritura lleva su llave de idempotencia.", 400)
        por_id = {p["id"]: p for p in self.productos}
        items = []
        for it in d.get("items") or []:
            try:
                pid = int(it.get("producto_id") or 0)
                cantidad = float(it.get("cantidad") or 0)
            except (TypeError, ValueError, AttributeError):
                return _err("dato_invalido", "Producto o cantidad no válidos.", 400)
            if pid not in por_id:
                return _err("producto_desconocido", "Producto desconocido.", 404)
            items.append((pid, cantidad))
        self.cuerpos_precompra.append(copy.deepcopy(d))
        hoy = self.hoy or datetime.now(ZoneInfo("America/Mexico_City")).date()
        try:
            entrega = date.fromisoformat(d["fecha_entrega"]) if d.get("fecha_entrega") else hoy
        except ValueError:
            return _err("rechazado", "Fecha no válida.", 409)
        if entrega < hoy:
            return _err("rechazado", "El pedido no puede ser para un día que ya pasó.", 409)
        contenido = (al["id"], t["tutor_ref"], sorted(items), entrega)
        previo = self.precompras.get((t["tutor_ref"], llave))
        if previo:
            if previo[0] != contenido:
                return _err("rechazado", f"La precompra {llave} ya se registró con otros datos.", 409)
            # Repetir no vuelve a aplicar: devuelve el pedido con los saldos ACTUALES.
            return httpx.Response(200, json={"ok": True, "pedido": copy.deepcopy(previo[1]),
                                             "alumno": copy.deepcopy(al)})
        lineas = []
        for pid, cantidad in items:
            if cantidad <= 0 or int(cantidad) != cantidad:
                return _err("rechazado", "Cantidad no válida.", 409)
            p = por_id[pid]
            lineas.append({"producto_id": pid, "producto": p["nombre"], "cantidad": int(cantidad),
                           "precio_unitario": p["precio"]})
        if not lineas:
            return _err("rechazado", "La precompra no tiene productos.", 409)
        total = round(sum(l["precio_unitario"] * l["cantidad"] for l in lineas), 2)
        if round(float(al["disponible"]), 2) < total:
            return _err("rechazado", f"Saldo insuficiente de {al['nombre']}: tiene "
                        f"${al['disponible']:,.2f} disponibles y se necesitan ${total:,.2f}.", 409)
        al["disponible"] = round(float(al["disponible"]) - total, 2)
        al["retenido"] = round(float(al["retenido"]) + total, 2)
        pedido = {"id": len(self.pedidos) + 1, "nombre": f"PC-{len(self.pedidos) + 1:05d}",
                  "alumno_id": al["id"], "fecha_entrega": entrega.isoformat(), "total": total,
                  "estado": "retenida", "lineas": lineas}
        self.pedidos.append(pedido)
        self.precompras[(t["tutor_ref"], llave)] = (contenido, pedido)  # el vivo, como el real
        self.movimientos[al["id"]].insert(0, {
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "tipo": "retencion",
            "descripcion": "Retención", "disponible": -total, "retenido": total,
            "venta": ""})
        return httpx.Response(200, json={"ok": True, "pedido": copy.deepcopy(pedido),
                                         "alumno": copy.deepcopy(al)})

    def _transferencia(self, d: dict) -> httpx.Response:
        """Imita op_transferencia + _sb_transferir del TPV real. Orden de comprobaciones como en
        el real: tutor, origen y destino (ajeno = 404 `alumno_desconocido`), importe (400
        `importe_invalido`), llave (400 `falta_llave`); luego el dominio, todo 409 `rechazado`:
        origen == destino, repetir con otro contenido ("ya se registró con otros datos") y saldo
        insuficiente. Repetir lo mismo NO re-aplica y devuelve los saldos ACTUALES (sin revisar
        saldo). El importe se redondea a centavos (Odoo: half-up), no se rechaza."""
        t = self.tutores.get(d.get("tutor_ref"))
        if not t or not t["activo"]:
            return _err("tutor_desconocido", "Tutor desconocido.", 404)
        propios = {a["id"]: a for a in t["alumnos"]}
        origen = propios.get(d.get("origen_id")) if isinstance(d.get("origen_id"), int) else None
        destino = propios.get(d.get("destino_id")) if isinstance(d.get("destino_id"), int) else None
        if origen is None or destino is None:
            return _err("alumno_desconocido", "Alumno desconocido.", 404)
        try:
            importe = float(d.get("importe"))
        except (TypeError, ValueError):
            return _err("importe_invalido", "Importe no válido.", 400)
        if importe != importe or importe in (float("inf"), float("-inf")) or importe <= 0:
            return _err("importe_invalido", "El importe tiene que ser mayor que cero.", 400)
        importe = float(Decimal(str(importe)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        if importe <= 0:
            return _err("importe_invalido", "El importe tiene que ser mayor que cero.", 400)
        llave = (d.get("llave") or "").strip() if isinstance(d.get("llave"), str) else ""
        if not llave or len(llave) > 120:
            return _err("falta_llave", "Toda escritura lleva su llave de idempotencia.", 400)
        self.cuerpos_transferencia.append(copy.deepcopy(d))
        if origen["id"] == destino["id"]:
            return _err("rechazado", "Origen y destino son el mismo alumno.", 409)
        contenido = (origen["id"], destino["id"], importe)
        previo = self.transferencias.get((t["tutor_ref"], llave))
        if previo is None:
            if round(float(origen["disponible"]), 2) < importe:
                return _err("rechazado", f"Saldo insuficiente de {origen['nombre']}: tiene "
                            f"${origen['disponible']:,.2f} disponibles y se necesitan "
                            f"${importe:,.2f}.", 409)
            origen["disponible"] = round(float(origen["disponible"]) - importe, 2)
            destino["disponible"] = round(float(destino["disponible"]) + importe, 2)
            self.transferencias[(t["tutor_ref"], llave)] = contenido
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.movimientos[origen["id"]].insert(0, {
                "fecha": ahora, "tipo": "transferencia", "descripcion": "Transferencia",
                "disponible": -importe, "retenido": 0, "venta": ""})
            self.movimientos[destino["id"]].insert(0, {
                "fecha": ahora, "tipo": "transferencia", "descripcion": "Transferencia",
                "disponible": importe, "retenido": 0, "venta": ""})
        elif previo != contenido:
            return _err("rechazado", f"La operación portal:{t['tutor_ref']}:{llave} ya se "
                        "registró con otros datos. No se aplica dos veces.", 409)
        return httpx.Response(200, json={"ok": True, "alumnos": [self._ficha(origen),
                                                                  self._ficha(destino)]})

    def _alumno_pedidos(self, d: dict) -> httpx.Response:
        """Como op_alumno_pedidos: los 50 más recientes del alumno (el real ordena `fecha desc,
        id desc`, es decir, por creación, no por día de entrega)."""
        t = self.tutores.get(d.get("tutor_ref"))
        if not t or not t["activo"]:
            return _err("tutor_desconocido", "Tutor desconocido.", 404)
        if d.get("alumno_id") not in [a["id"] for a in t["alumnos"]]:
            return _err("alumno_desconocido", "Alumno desconocido.", 404)
        suyos = [p for p in reversed(self.pedidos) if p["alumno_id"] == d["alumno_id"]][:50]
        return httpx.Response(200, json={"ok": True, "pedidos": copy.deepcopy(suyos)})

    def _cancelar(self, d: dict) -> httpx.Response:
        """Imita op_precompra_cancelar envuelto en _sb_una_vez: la autorización se revisa SIEMPRE
        (también al repetir), repetir devuelve la respuesta GUARDADA sin volver a ejecutar, la
        misma llave con otro contenido es 409 `llave_reutilizada`, cancelar lo ya cancelado
        es OK sin cambios y cualquier otro estado distinto de `retenida` es 409 `rechazado`
        (que no se guarda)."""
        t = self.tutores.get(d.get("tutor_ref"))
        if not t or not t["activo"]:
            return _err("tutor_desconocido", "Tutor desconocido.", 404)
        llave = (d.get("llave") or "").strip() if isinstance(d.get("llave"), str) else ""
        if not llave or len(llave) > 120:
            return _err("falta_llave", "Toda escritura lleva su llave de idempotencia.", 400)
        pedido = next((p for p in self.pedidos if p["id"] == d.get("pedido_id")), None)
        if pedido is None or pedido["alumno_id"] not in [a["id"] for a in t["alumnos"]]:
            return _err("pedido_desconocido", "Pedido desconocido.", 404)
        self.cuerpos_cancelar.append(copy.deepcopy(d))
        huella = json.dumps(["precompra_cancelar", d], sort_keys=True, default=str)
        previa = self.cancelaciones.get((t["tutor_ref"], llave))
        if previa:
            if previa[0] != huella:
                return _err("llave_reutilizada", "Esa llave ya se usó para otra operación.", 409)
            return httpx.Response(200, json=copy.deepcopy(previa[1]))
        al = self.alumnos[pedido["alumno_id"]]
        if pedido["estado"] != "cancelada":
            if pedido["estado"] != "retenida":
                return _err("rechazado", f"El pedido {pedido['nombre']} ya no se puede cancelar.", 409)
            al["disponible"] = round(float(al["disponible"]) + pedido["total"], 2)
            al["retenido"] = round(float(al["retenido"]) - pedido["total"], 2)
            pedido["estado"] = "cancelada"
            self.movimientos[al["id"]].insert(0, {
                "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "tipo": "liberacion",
                "descripcion": "Liberación", "disponible": pedido["total"],
                "retenido": -pedido["total"], "venta": ""})
        resp = {"ok": True, "pedido": copy.deepcopy(pedido), "alumno": copy.deepcopy(al)}
        self.cancelaciones[(t["tutor_ref"], llave)] = (huella, copy.deepcopy(resp))
        return httpx.Response(200, json=resp)

    def _operar(self, op: str, d: dict) -> httpx.Response:
        if op in ("tutor/alumnos", "alumno/movimientos"):
            return self._lectura_tutor(op, d)
        if op == "transferencia":
            return self._transferencia(d)
        if op == "alumno/pedidos":
            return self._alumno_pedidos(d)
        if op == "precompra/cancelar":
            return self._cancelar(d)
        if op == "alumno/permiso":
            return self._permiso(d)
        if op in ("credencial/bloquear", "credencial/desbloquear"):
            return self._credencial(op, d)
        if op == "admin/tutor/detalle":
            t = self.tutores.get(d.get("tutor_ref"))
            if not t:
                return _err("no_encontrado", "Tutor desconocido.", 404)
            return httpx.Response(200, json={"ok": True, "tutor": t})
        if op == "menu":
            return httpx.Response(200, json={"ok": True, "productos": copy.deepcopy(self.productos)})
        if op == "precompra":
            return self._precompra(d)
        if op == "recarga":
            return self._recarga(d)
        if op not in ("admin/tutor/crear", "admin/tutor/editar", "admin/alumno/crear", "admin/vincular"):
            return _err("no_encontrada", "Operación desconocida.", 404)
        llave = d.get("llave")
        if not llave:
            return _err("datos_invalidos", "Falta la llave.", 400)
        previo = self.guardado.get(llave)
        if previo:
            if previo[0] != op or previo[1] != d:
                return _err("llave_reutilizada", "Llave usada con otro contenido.", 409)
            return httpx.Response(200, json=copy.deepcopy(previo[2]))
        if op == "admin/tutor/crear":
            ref = f"T{len(self.tutores) + 1}"
            self.tutores[ref] = {"tutor_ref": ref, "nombre": d["nombre"], "telefono": d.get("telefono"),
                                 "correo": d.get("correo"), "activo": True, "alumnos": []}
            resp = {"ok": True, "tutor": self.tutores[ref]}
        elif op == "admin/tutor/editar":
            t = self.tutores.get(d.get("tutor_ref"))
            if not t:
                return _err("no_encontrado", "Tutor desconocido.", 404)
            for campo in ("nombre", "telefono", "correo"):
                if campo in d:
                    t[campo] = d[campo]
            resp = {"ok": True, "tutor": t}
        elif op == "admin/alumno/crear":
            for ref in d.get("tutores", []):
                if ref not in self.tutores:
                    return _err("no_encontrado", "Tutor desconocido.", 404)
            aid = len(self.alumnos) + 1
            nombre = d.get("nombre") or " ".join(
                x for x in (d.get("nombres"), d.get("apellido_paterno"), d.get("apellido_materno")) if x)
            al = {"id": aid, "nombre": nombre, "nombres": d.get("nombres"),
                  "matricula": d.get("matricula"),
                  "codigo": f"C{aid:04d}", "compra_sin_pedido": True, "disponible": 0,
                  "retenido": 0, "credenciales": []}
            self.alumnos[aid] = al
            for ref in d.get("tutores", []):
                self.tutores[ref]["alumnos"].append(al)
            resp = {"ok": True, "alumno": al}
        else:
            t = self.tutores.get(d.get("tutor_ref"))
            al = self.alumnos.get(d.get("alumno_id"))
            if not t or not al:
                return _err("no_encontrado", "Tutor o alumno desconocido.", 404)
            activo = d.get("activo", True)
            t["alumnos"] = [a for a in t["alumnos"] if a["id"] != al["id"]]
            if activo:
                t["alumnos"].append(al)
            resp = {"ok": True, "tutor": t}
        self.guardado[llave] = (op, d, copy.deepcopy(resp))  # instantánea: no comparte estado vivo
        return httpx.Response(200, json=resp)
