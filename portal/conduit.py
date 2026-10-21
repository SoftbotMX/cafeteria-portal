"""Envío de WhatsApp por Conduit (POST <url>/api/notify, firmado con el broker_secret)."""
import hashlib
import hmac
import json
import time

import httpx


class ErrorEnvio(Exception):
    pass


def enviar_whatsapp(url: str, client_id: str, secreto: str, telefono10: str, texto: str, llave: str,
                    *, transport: httpx.BaseTransport | None = None) -> str:
    if not (len(telefono10) == 10 and telefono10.isascii() and telefono10.isdigit()):
        raise ErrorEnvio("El teléfono debe tener exactamente 10 dígitos.")
    cuerpo = json.dumps({
        "client_id": client_id, "recipient": "52" + telefono10, "channel": "whatsapp",
        "text": texto, "idempotency_key": llave,
    }, separators=(",", ":"), ensure_ascii=False).encode()
    ts = str(int(time.time()))
    firma = hmac.new(secreto.encode(), f"{ts}.".encode() + cuerpo, hashlib.sha256).hexdigest()
    cab = {"Content-Type": "application/json", "X-Conduit-Timestamp": ts,
           "X-Conduit-Signature": "hmac-sha256=" + firma}
    try:
        with httpx.Client(transport=transport, timeout=10) as http:
            r = http.post(url.rstrip("/") + "/api/notify", content=cuerpo, headers=cab)
    except httpx.HTTPError as e:
        raise ErrorEnvio(f"No se pudo contactar a Conduit ({type(e).__name__}).") from None
    if r.status_code == 200:  # incluso con duplicate:true
        return "enviado"
    if r.status_code == 409:
        return "en_curso"
    raise ErrorEnvio(f"Conduit respondió {r.status_code}.")
