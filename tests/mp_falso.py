"""MercadoPago simulado: preferencias y pagos en memoria, con el token del operador."""
import copy
import json

import httpx


class MPFalso:
    def __init__(self, token: str = "TEST-token"):
        self.token = token
        self.preferencias: dict[str, dict] = {}  # id -> cuerpo recibido
        self.idempotencia: dict[str, str] = {}  # X-Idempotency-Key -> id de preferencia
        self.pagos: dict[str, dict] = {}
        self.consultas: list[str] = []  # ids de pago consultados
        self.busquedas = 0
        self.fallar: set[str] = set()  # "preferencia" | "pago" | "buscar"
        # La cuenta de cada token aceptado por /users/me (el de pagos y los que se agreguen).
        self.cuentas: dict[str, dict] = {token: {"id": 123456, "nickname": "CAFETERIA_PRUEBA",
                                                 "email": "prueba@cafeteria.test",
                                                 "site_id": "MLM", "tags": ["normal"]}}
        self.transport = httpx.MockTransport(self._manejar)

    def pagar(self, external_reference, monto, status="approved", moneda="MXN") -> str:
        pid = str(9_000_000 + len(self.pagos) + 1)
        self.pagos[pid] = {"id": int(pid), "status": status, "status_detail": "accredited",
                           "currency_id": moneda, "transaction_amount": monto,
                           "external_reference": str(external_reference)}
        return pid

    def _manejar(self, req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/users/me":
            token = req.headers.get("Authorization", "").removeprefix("Bearer ")
            if token not in self.cuentas:
                return httpx.Response(401, json={"message": "invalid access token"})
            return httpx.Response(200, json=self.cuentas[token])
        if req.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "invalid access token"})
        ruta = req.url.path
        if req.method == "POST" and ruta == "/checkout/preferences":
            if "preferencia" in self.fallar:
                return httpx.Response(500, json={"message": "error"})
            clave = req.headers.get("X-Idempotency-Key")
            if clave and clave in self.idempotencia:
                pid = self.idempotencia[clave]
            else:
                pid = f"PREF-{len(self.preferencias) + 1}"
                self.preferencias[pid] = json.loads(req.content)
                if clave:
                    self.idempotencia[clave] = pid
            return httpx.Response(201, json={
                "id": pid, "init_point": f"https://mp.test/checkout?pref_id={pid}",
                "sandbox_init_point": f"https://sandbox.mp.test/checkout?pref_id={pid}"})
        if req.method == "GET" and ruta == "/v1/payments/search":
            if "buscar" in self.fallar:
                return httpx.Response(500, json={"message": "error"})
            self.busquedas += 1
            ref = req.url.params.get("external_reference")
            res = [copy.deepcopy(p) for p in self.pagos.values() if p["external_reference"] == ref]
            res.sort(key=lambda p: p["id"], reverse=True)  # sort=date_created, criteria=desc
            limite = int(req.url.params.get("limit", 30))
            desde = int(req.url.params.get("offset", 0))
            return httpx.Response(200, json={"results": res[desde:desde + limite], "paging": {
                "total": len(res), "limit": limite, "offset": desde}})
        if req.method == "GET" and ruta.startswith("/v1/payments/"):
            pid = ruta.rsplit("/", 1)[1]
            self.consultas.append(pid)
            if "pago" in self.fallar:
                return httpx.Response(500, json={"message": "error"})
            if pid not in self.pagos:
                return httpx.Response(404, json={"message": "Payment not found"})
            return httpx.Response(200, json=copy.deepcopy(self.pagos[pid]))
        return httpx.Response(404, json={"message": "not found"})
