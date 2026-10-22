"""Cliente mínimo de la API de MercadoPago (Checkout Pro): crear la preferencia y consultar pagos.

El token es el Access Token del operador (cifrado en BD). Nunca se registra.
"""
import re

import httpx

API = "https://api.mercadopago.com"
_ID_PAGO = re.compile(r"[0-9]{1,20}")


class ErrorMP(Exception):
    def __init__(self, status: int, mensaje: str):
        super().__init__(f"{status}: {mensaje}")
        self.status = status  # 0 = sin conexión
        self.mensaje = mensaje


class ClienteMP:
    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 10):
        self._token = token
        self._transport = transport
        self._timeout = timeout

    def __repr__(self) -> str:
        return "ClienteMP()"

    def _pedir(self, metodo: str, ruta: str, *, json=None, params=None, cabeceras=None) -> dict:
        h = {"Authorization": f"Bearer {self._token}", **(cabeceras or {})}
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as http:
                r = http.request(metodo, API + ruta, json=json, params=params, headers=h)
        except httpx.HTTPError as e:
            raise ErrorMP(0, f"Sin conexión con MercadoPago ({type(e).__name__}).") from None
        try:
            datos = r.json()
        except ValueError:
            datos = None
        if r.status_code in (200, 201) and isinstance(datos, dict):
            return datos
        mensaje = datos.get("message") if isinstance(datos, dict) else None
        raise ErrorMP(r.status_code, str(mensaje or "Respuesta inesperada de MercadoPago."))

    def crear_preferencia(self, cuerpo: dict, *, idempotencia: str) -> dict:
        """Con la misma clave de idempotencia, MP devuelve la misma preferencia (doble envío)."""
        return self._pedir("POST", "/checkout/preferences", json=cuerpo,
                           cabeceras={"X-Idempotency-Key": idempotencia})

    def pago(self, pago_id: str) -> dict:
        pago_id = str(pago_id).strip()
        if not _ID_PAGO.fullmatch(pago_id):
            raise ErrorMP(400, "Id de pago no válido.")
        return self._pedir("GET", f"/v1/payments/{pago_id}")

    def buscar_por_referencia(self, referencia: str, paginas: int = 10) -> list[dict]:
        """Todos los pagos de la referencia (recorre las páginas; hasta 1,000)."""
        pagos: list[dict] = []
        for n in range(paginas):
            r = self._pedir("GET", "/v1/payments/search", params={
                "external_reference": referencia, "sort": "date_created", "criteria": "desc",
                "limit": 100, "offset": n * 100})
            res = r.get("results")
            res = [p for p in res if isinstance(p, dict)] if isinstance(res, list) else []
            pagos += res
            total = (r.get("paging") or {}).get("total") if isinstance(r.get("paging"), dict) else None
            if len(res) < 100 or not isinstance(total, int) or len(pagos) >= total:
                break
        return pagos
