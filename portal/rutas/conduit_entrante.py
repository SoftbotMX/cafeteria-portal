"""Destino de Conduit (Broker Mode): lo que el papá escribe al WhatsApp de la cafetería.

`/validate` acepta a TODO número — cada rechazo cuenta para el silencio permanente de Conduit
(#17); a un número no registrado le contesta el bot. `/message` responde 200 y, después, el bot
guiado (`portal.bot`) arma la respuesta y sale por `/api/broker/reply`. Conduit firma con el
`broker_secret` del operador (el mismo con que el portal firma `/api/notify`).
"""
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portal import bot
from portal.conduit import ErrorEnvio, firma_valida, responder
from portal.models import BotMensaje, Operador
from portal.sesiones import operador_actual

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/conduit/incoming")

MSG_FALLA = "Algo salió mal de nuestro lado. Intenta de nuevo en un momento."
ABANDONADO = timedelta(minutes=2)  # un registro sin respuesta más viejo: el proceso murió


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


def _atender_y_contestar(app, operador_id: int, sender: str, texto: str, message_id: str) -> None:
    """En segundo plano (hilo del pool): el bot habla con el TPV y MercadoPago, que tardan."""
    with Session(app.state.engine) as db:
        operador = db.get(Operador, operador_id)
        # Cada message_id una vez (Codex): el repetido recibe la misma respuesta sin volver a
        # pasar por el bot; si el primero sigue en curso, contesta el primero.
        try:
            registro = BotMensaje(operador_id=operador_id, message_id=message_id)
            db.add(registro)
            db.commit()
        except IntegrityError:
            db.rollback()
            previo = db.scalar(select(BotMensaje).where(BotMensaje.operador_id == operador_id,
                                                        BotMensaje.message_id == message_id))
            if previo is None:
                return
            if previo.respuesta is not None:
                respuesta = previo.respuesta
            else:
                # Sin respuesta: en curso, o abandonado si el proceso murió (Codex). Sólo uno lo
                # reclama (UPDATE condicional); las llaves de cada operación evitan duplicar.
                reclamo = db.execute(update(BotMensaje).where(
                    BotMensaje.id == previo.id, BotMensaje.respuesta.is_(None),
                    BotMensaje.creado < datetime.now(timezone.utc) - ABANDONADO)
                    .values(creado=func.now()))
                db.commit()
                if reclamo.rowcount != 1:
                    return
                registro = previo
                respuesta = None
        else:
            respuesta = None
        if respuesta is None:
            registro_id = registro.id
            try:
                # El bot guarda la respuesta junto con el paso de la conversación.
                respuesta = bot.atender(db, app, operador, sender, texto, registro_id)
            except Exception:  # noqa: BLE001 — el papá no se queda sin respuesta
                db.rollback()
                log.exception("bot: falló el mensaje %s", message_id)
                respuesta = MSG_FALLA
            registro = db.get(BotMensaje, registro_id)
            if registro.respuesta is None:  # número no registrado, ocupado o falla
                registro.respuesta = respuesta
                db.commit()
            operador = db.get(Operador, operador_id)
        secreto = operador.secreto_conduit(app.state.settings.fernet_key)
        try:
            responder(operador.conduit_url, secreto, message_id, respuesta,
                      transport=app.state.conduit_transport)
        except ErrorEnvio as e:
            log.warning("Conduit: no se pudo contestar %s: %s", message_id, e)


@router.post("/message")
async def mensaje(request: Request, tareas: BackgroundTasks,
                  operador: Operador = Depends(operador_actual)):
    datos = await _cuerpo_firmado(request, operador)
    message_id = datos.get("message_id")
    sender = datos.get("sender")
    if not isinstance(message_id, str) or not message_id or len(message_id) > 100 \
            or not isinstance(sender, str):
        raise HTTPException(400)
    texto = datos.get("text") if isinstance(datos.get("text"), str) else ""
    # Se contesta DESPUÉS de responder 200: Conduit ya dejó el mensaje `pending` antes de
    # reenviarlo, y así no se le retiene el reenvío mientras el bot habla con el TPV.
    tareas.add_task(_atender_y_contestar, request.app, operador.id, sender, texto[:1000],
                    message_id)
    return {"received": True}
