"""Destino de Conduit (Broker Mode): lo que el papá escribe al WhatsApp de la cafetería.

Día 1 (antes del bot): `/validate` acepta a TODO número — cada rechazo cuenta para el silencio
permanente de Conduit (#17) y los papás van a contestar «gracias» a los avisos — y `/message`
contesta con la liga del portal. Conduit firma con el `broker_secret` del operador (el mismo con
que el portal firma `/api/notify`).
"""
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from portal.conduit import ErrorEnvio, firma_valida, responder
from portal.models import Operador
from portal.sesiones import operador_actual

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conduit/incoming")

TEXTO_DIA_1 = ("Hola. Este número sólo manda avisos de la cafetería. Para consultar el saldo, "
               "pedir o recargar entra a {url}")


async def _cuerpo_firmado(request: Request, operador: Operador) -> dict:
    secreto = operador.secreto_conduit(request.app.state.settings.fernet_key)
    if not (secreto and operador.conduit_url):
        raise HTTPException(404)
    crudo = await request.body()
    if not firma_valida(secreto, request.headers.get("X-Conduit-Timestamp"),
                        request.headers.get("X-Conduit-Signature"), crudo):
        raise HTTPException(401)
    try:
        datos = json.loads(crudo)
    except ValueError:
        raise HTTPException(400) from None
    if not isinstance(datos, dict):
        raise HTTPException(400)
    return datos


@router.post("/validate")
async def validar(request: Request, operador: Operador = Depends(operador_actual)):
    await _cuerpo_firmado(request, operador)
    return {"valid": True}


def _contestar(app, operador_url: str, secreto: str, message_id: str, texto: str) -> None:
    try:
        responder(operador_url, secreto, message_id, texto, transport=app.state.conduit_transport)
    except ErrorEnvio as e:
        log.warning("Conduit: no se pudo contestar %s: %s", message_id, e)


@router.post("/message")
async def mensaje(request: Request, tareas: BackgroundTasks,
                  operador: Operador = Depends(operador_actual)):
    datos = await _cuerpo_firmado(request, operador)
    message_id = datos.get("message_id")
    if not isinstance(message_id, str) or not message_id or len(message_id) > 100 \
            or not isinstance(datos.get("sender"), str):
        raise HTTPException(400)
    # Se contesta DESPUÉS de responder 200: Conduit ya dejó el mensaje `pending` antes de
    # reenviarlo, y así no se le retiene el reenvío mientras se manda la respuesta.
    url = str(request.base_url).rstrip("/")
    secreto = operador.secreto_conduit(request.app.state.settings.fernet_key)
    tareas.add_task(_contestar, request.app, operador.conduit_url, secreto, message_id,
                    TEXTO_DIA_1.format(url=url))
    return {"received": True}
