"""Barrido de recargas (servicio aparte en el compose): `python -m portal.barrido`.

Cada minuto: las pagadas que el TPV no tomó se reintentan; las pendientes de más de 10 minutos
(sin aviso de MP) se buscan en MP; las de más de 24 h sin pago vencen. Cada recarga en su propia
transacción y con su candado: una que falla no detiene a las demás.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from portal import recargas
from portal.models import BotMensaje, Operador, Recarga

log = logging.getLogger("portal.barrido")
ESPERA_PENDIENTE = timedelta(minutes=10)
CADA = 60
LOTE = 200


def una_vuelta(app: FastAPI) -> int:
    """Revisa lo pendiente. Devuelve cuántas recargas tocó."""
    engine = app.state.engine
    ahora = datetime.now(timezone.utc)
    limite = ahora - ESPERA_PENDIENTE
    with Session(engine) as db:
        # Los message_id del bot sólo sirven para reconocer repetidos recientes.
        db.execute(delete(BotMensaje).where(BotMensaje.creado < ahora - timedelta(days=2)))
        db.commit()
        ids = db.execute(select(Recarga.id, Recarga.operador_id, Recarga.estado).where(or_(
            Recarga.estado == "aprobada",
            and_(Recarga.estado == "pendiente", Recarga.creada < limite),
            and_(Recarga.estado.in_(("acreditada", "revision")), ~Recarga.conciliada,
                 Recarga.liga_vence < ahora - recargas.CONCILIAR_TRAS)))
            # Las que llevan más sin intentarse primero: una que falla siempre no tapa a las demás.
            .order_by(Recarga.actualizada, Recarga.id).limit(LOTE)).all()
    for recarga_id, operador_id, estado in ids:
        with Session(engine) as db:
            # El intento se marca en su propia transacción: si el trabajo revienta, igual cuenta
            # y la recarga pasa al final de la fila (Codex).
            db.execute(update(Recarga).where(Recarga.id == recarga_id)
                       .values(actualizada=func.clock_timestamp()))
            db.commit()
            try:
                operador = db.get(Operador, operador_id)
                if estado in ("acreditada", "revision"):
                    recargas.conciliar(db, app, operador, recarga_id)
                else:
                    recargas.reintentar(db, app, operador, recarga_id, saltar=True)
                db.commit()
            except Exception:  # noqa: BLE001 — una recarga rara no detiene el barrido
                db.rollback()
                log.exception("barrido: la recarga %s falló", recarga_id)
    return len(ids)


def main() -> None:  # pragma: no cover — bucle del servicio
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from portal.app import create_app
    app = create_app()
    log.info("barrido de recargas: cada %s s", CADA)
    while True:
        try:
            n = una_vuelta(app)
            if n:
                log.info("barrido: %s recargas revisadas", n)
        except Exception:  # noqa: BLE001 — p. ej. la BD reiniciando: se intenta al minuto
            log.exception("barrido: vuelta fallida")
        time.sleep(CADA)


if __name__ == "__main__":  # pragma: no cover
    main()
