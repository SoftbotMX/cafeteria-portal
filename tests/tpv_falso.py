"""TPV simulado: verifica la firma como el real (una sola vez por firma) y guarda por llave."""
import copy
import hashlib
import hmac
import json
import time

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
        self.movimientos: dict[int, list[dict]] = {}  # más reciente primero, como el real
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

    def _lectura_tutor(self, op: str, d: dict) -> httpx.Response | None:
        t = self.tutores.get(d.get("tutor_ref"))
        if not t or not t["activo"]:
            return _err("tutor_desconocido", "Tutor desconocido.", 404)
        if op == "tutor/alumnos":
            return httpx.Response(200, json={"ok": True, "alumnos": copy.deepcopy(t["alumnos"])})
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

    def _operar(self, op: str, d: dict) -> httpx.Response:
        if op in ("tutor/alumnos", "alumno/movimientos"):
            return self._lectura_tutor(op, d)
        if op == "admin/tutor/detalle":
            t = self.tutores.get(d.get("tutor_ref"))
            if not t:
                return _err("no_encontrado", "Tutor desconocido.", 404)
            return httpx.Response(200, json={"ok": True, "tutor": t})
        if op == "menu":
            return httpx.Response(200, json={"ok": True, "productos": []})
        if op not in ("admin/tutor/crear", "admin/alumno/crear", "admin/vincular"):
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
        elif op == "admin/alumno/crear":
            for ref in d.get("tutores", []):
                if ref not in self.tutores:
                    return _err("no_encontrado", "Tutor desconocido.", 404)
            aid = len(self.alumnos) + 1
            al = {"id": aid, "nombre": d["nombre"], "matricula": d.get("matricula"),
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
