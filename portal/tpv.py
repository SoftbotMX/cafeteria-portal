"""Cliente firmado de la API del TPV (docs/API_TPV.md)."""
import hashlib
import hmac
import json
import threading
import time

import httpx
from sqlalchemy.orm import Session

from portal.models import Bitacora, Operador

PREFIJO = "/sb_cafe/api/v1/"

# El TPV acepta cada firma una vez y la firma depende del segundo: dos llamadas idénticas
# en el mismo segundo (p. ej. un reintento) darían la misma firma y el TPV respondería 409.
# Sólo si la firma ya se emitió se adelanta el timestamp 1 s, sin salirse de la ventana (300 s).
_emitidas: dict[str, int] = {}
_candado = threading.Lock()
_ADELANTO_MAX = 60


def _firma_unica(secreto: str, ruta: str, cuerpo: bytes) -> tuple[str, str]:
    ahora = int(time.time())
    with _candado:
        for k in [k for k, t in _emitidas.items() if t < ahora - 600]:
            del _emitidas[k]
        ts = ahora
        while True:
            firma = firmar_tpv(secreto, str(ts), ruta, cuerpo)
            if firma not in _emitidas:
                _emitidas[firma] = ts
                return str(ts), firma
            ts += 1
            if ts - ahora > _ADELANTO_MAX:
                raise ErrorTPV(0, "demasiadas_llamadas", "Demasiadas llamadas idénticas seguidas.")


class ErrorTPV(Exception):
    def __init__(self, status: int, codigo: str, mensaje: str):
        super().__init__(f"{status} {codigo}: {mensaje}")
        self.status = status
        self.codigo = codigo
        self.mensaje = mensaje


def firmar_tpv(secreto: str, ts: str, ruta: str, cuerpo: bytes) -> str:
    """hex de HMAC-SHA256(secreto, ts + "." + ruta + "." + cuerpo), igual que el TPV."""
    mensaje = f"{ts}.{ruta}.".encode() + cuerpo
    return hmac.new(secreto.encode(), mensaje, hashlib.sha256).hexdigest()


class ClienteTPV:
    def __init__(self, base_url: str, secreto: str, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 10):
        self.base_url = base_url.rstrip("/")
        self._secreto = secreto
        self._transport = transport
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"ClienteTPV({self.base_url!r})"

    def llamar(self, operacion: str, cuerpo: dict) -> dict:
        # `repetida` = otra petición (de otro worker) emitió la misma firma en el mismo segundo.
        # El TPV la rechaza antes de ejecutar nada, así que re-firmar y repetir es seguro.
        for intento in range(3):
            try:
                return self._una(operacion, cuerpo)
            except ErrorTPV as e:
                if e.codigo != "repetida" or intento == 2:
                    raise
        raise AssertionError("inalcanzable")

    def _una(self, operacion: str, cuerpo: dict) -> dict:
        crudo = json.dumps(cuerpo, separators=(",", ":"), ensure_ascii=False).encode()
        ruta = PREFIJO + operacion
        ts, firma = _firma_unica(self._secreto, ruta, crudo)  # se firma en cada llamada
        cabeceras = {
            "Content-Type": "application/json",
            "X-SB-Cafe-Timestamp": ts,
            "X-SB-Cafe-Signature": "hmac-sha256=" + firma,
        }
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as http:
                r = http.post(self.base_url + ruta, content=crudo, headers=cabeceras)
        except httpx.HTTPError as e:
            raise ErrorTPV(0, "sin_conexion", f"No se pudo contactar al TPV ({type(e).__name__}).") from None
        try:
            datos = r.json()
        except ValueError:
            datos = None
        if not isinstance(datos, dict):
            raise ErrorTPV(r.status_code, "respuesta_invalida", "El TPV respondió algo inesperado.")
        if r.status_code == 200 and datos.get("ok") is True:
            return datos
        err = datos.get("error") if isinstance(datos.get("error"), dict) else {}
        raise ErrorTPV(r.status_code, str(err.get("codigo") or "respuesta_invalida"),
                       str(err.get("mensaje") or "Error del TPV."))


def cliente_admin(app, operador: Operador, llave_fernet: str) -> ClienteTPV:
    return ClienteTPV(operador.tpv_url, operador.secreto_tpv_admin(llave_fernet),
                      transport=app.state.tpv_transport)


def cliente_tutor(app, operador: Operador, llave_fernet: str) -> ClienteTPV:
    return ClienteTPV(operador.tpv_url, operador.secreto_tpv(llave_fernet),
                      transport=app.state.tpv_transport)


def registrar_escritura(db: Session, operador_id: int, actor: str, operacion: str, llave: str,
                        error: ErrorTPV | None) -> None:
    db.add(Bitacora(operador_id=operador_id, actor=actor, operacion=operacion, llave=llave,
                    http_status=error.status if error else 200,
                    codigo_error=error.codigo if error else None))
    db.commit()
