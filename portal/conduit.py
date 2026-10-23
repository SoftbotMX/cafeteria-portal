"""Envío de WhatsApp por Conduit (POST <url>/api/notify, firmado con el broker_secret)."""
import hashlib
import hmac
import json
import time

import httpx


class ErrorEnvio(Exception):
    pass


def plantilla(nombre: str, valores: list[str], botones: list[dict] | None = None) -> dict:
    """Plantilla aprobada en Meta (`docs/PLANTILLAS_WHATSAPP.md`). Las variables no pueden ir
    vacías ni llevar saltos de línea (Meta las rechaza)."""
    limpios = [" ".join(str(v).split()) or "-" for v in valores]
    componentes = [{"type": "body", "parameters": [{"type": "text", "text": v} for v in limpios]}]
    return {"name": nombre, "language": "es_MX", "components": componentes + (botones or [])}


def enviar_whatsapp(url: str, client_id: str, secreto: str, telefono10: str, texto: str, llave: str,
                    *, transport: httpx.BaseTransport | None = None,
                    plantilla: dict | None = None) -> str:
    """Con `plantilla`, Conduit manda la plantilla y no el texto (Conduit #42): llega aunque el papá
    no haya escrito en 24 h. Un 400 entonces es definitivo (plantilla no aprobada, número sin
    WhatsApp…) y trae el motivo de Meta."""
    if not (len(telefono10) == 10 and telefono10.isascii() and telefono10.isdigit()):
        raise ErrorEnvio("El teléfono debe tener exactamente 10 dígitos.")
    datos = {"client_id": client_id, "recipient": "52" + telefono10, "channel": "whatsapp",
             "idempotency_key": llave}
    if plantilla:
        datos["template"] = plantilla
    else:
        datos["text"] = texto
    cuerpo = json.dumps(datos, separators=(",", ":"), ensure_ascii=False).encode()
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
    if r.status_code == 400 and plantilla:
        try:
            motivo = r.json().get("detail")
        except (ValueError, AttributeError):
            motivo = None
        raise ErrorEnvio(f"WhatsApp rechazó la plantilla: {motivo or 'sin motivo'}.")
    raise ErrorEnvio(f"Conduit respondió {r.status_code}.")


# -- Broker Mode: Conduit nos reenvía lo que escribe el papá -----------------------------------
VENTANA_FIRMA = 300  # s, igual que Conduit (anti-replay)
ESPERAS_RESPUESTA = (0.5, 2.0)  # entre reintentos de /api/broker/reply


def firma_valida(secreto: str, ts: str | None, firma: str | None, cuerpo: bytes,
                 ahora: float | None = None) -> bool:
    """HMAC-SHA256(secreto, ts + "." + cuerpo) sobre los bytes EXACTOS recibidos."""
    if not ts or not firma or not ts.isascii() or not ts.isdigit():
        return False
    if abs((ahora if ahora is not None else time.time()) - int(ts)) > VENTANA_FIRMA:
        return False
    esperada = "hmac-sha256=" + hmac.new(
        secreto.encode(), f"{ts}.".encode() + cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(firma.encode(), esperada.encode())


def responder(url: str, secreto: str, message_id: str, texto: str, *,
              transport: httpx.BaseTransport | None = None) -> str:
    """Contesta un mensaje reenviado (POST <url>/api/broker/reply). Reintenta lo que Conduit
    marca como reintentable (409; 502/503 con `retryable`). `cacheable: false`: la respuesta
    depende de quién escribe y cuándo, no del texto."""
    cuerpo = json.dumps({"message_id": message_id, "reply_text": texto, "cacheable": False},
                        separators=(",", ":"), ensure_ascii=False).encode()
    for intento in range(len(ESPERAS_RESPUESTA) + 1):
        ts = str(int(time.time()))
        firma = hmac.new(secreto.encode(), f"{ts}.".encode() + cuerpo, hashlib.sha256).hexdigest()
        cab = {"Content-Type": "application/json", "X-Conduit-Timestamp": ts,
               "X-Conduit-Signature": "hmac-sha256=" + firma}
        try:
            with httpx.Client(transport=transport, timeout=10) as http:
                r = http.post(url.rstrip("/") + "/api/broker/reply", content=cuerpo, headers=cab)
        except httpx.HTTPError:
            r = None
        if r is not None and r.status_code == 200:
            return "enviado"
        reintentable = r is None or r.status_code == 409 or (
            r.status_code in (502, 503) and _retryable(r))
        if not reintentable or intento == len(ESPERAS_RESPUESTA):
            raise ErrorEnvio(f"Conduit no aceptó la respuesta ({r.status_code if r else 'sin conexión'}).")
        time.sleep(ESPERAS_RESPUESTA[intento])
    raise AssertionError("inalcanzable")


def _retryable(r: httpx.Response) -> bool:
    try:
        datos = r.json()
    except ValueError:
        return False
    return isinstance(datos, dict) and datos.get("retryable") is True
