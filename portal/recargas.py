"""Recargas con MercadoPago (Checkout Pro).

Tres caminos llegan aquí con un pago: el aviso de MP, el regreso del papá y el barrido. Ninguno le
cree al que avisa: el pago se consulta en la API de MP y se acredita sólo si está aprobado, en
pesos, por el total exacto y es de una recarga de ESTE operador. El tutor, el reparto y el total
salen de la fila `recarga`, nunca del aviso. La fila se toma con candado (`FOR UPDATE`): dos
caminos a la vez no acreditan dos veces (y el TPV además es idempotente por `pago_ref`).
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portal.mp import ClienteMP, ErrorMP
from portal.models import Operador, Recarga, Tutor
from portal.tpv import ErrorTPV, cliente_tutor, registrar_escritura
from portal.tutor_tpv import es_incierto

log = logging.getLogger(__name__)

MINIMO, MAXIMO = 50, 2000
VIGENCIA_PAGO = timedelta(minutes=30)
VENCE_PENDIENTE = timedelta(hours=24)
CONCILIAR_TRAS = timedelta(minutes=15)  # después de que vence la liga de pago
EXCLUIDOS = ("ticket", "atm", "bank_transfer")  # OXXO, cajeros y SPEI: no son inmediatos
_ENTERO = re.compile(r"[0-9]{1,4}")
_REFERENCIA = re.compile(r"[0-9]{1,12}")

MSG_IMPORTES = (f"Escribe cuánto le recargas a cada hijo en pesos enteros (sin centavos). El total "
                f"tiene que ser de ${MINIMO} a ${MAXIMO:,}.")


def cliente_mp(app: FastAPI, operador: Operador) -> ClienteMP | None:
    token = operador.token_mp(app.state.settings.fernet_key)
    if not token:
        return None
    return ClienteMP(token, transport=getattr(app.state, "mp_transport", None))


def armar_reparto(hijos: list[dict], datos: dict[str, str]) -> tuple[list[dict], Decimal] | str:
    """Del formulario (`importe_<id>` por hijo) al reparto. Devuelve el mensaje si no vale."""
    reparto = []
    for h in hijos:
        if not isinstance(h, dict) or not isinstance(h.get("id"), int):
            continue
        texto = (datos.get(f"importe_{h['id']}") or "").strip()
        if not texto:
            continue
        if not _ENTERO.fullmatch(texto):
            return MSG_IMPORTES
        if int(texto) > 0:
            reparto.append({"alumno_id": h["id"], "nombre": h.get("nombre") or "",
                            "importe": int(texto)})
    total = sum(p["importe"] for p in reparto)
    if not reparto or not MINIMO <= total <= MAXIMO:
        return MSG_IMPORTES
    return reparto, Decimal(total)


def _preferencia(recarga: Recarga, base_url: str, ahora: datetime) -> dict:
    regreso = f"{base_url}/recargas/{recarga.id}"
    return {
        "items": [{"id": "recarga", "title": "Recarga de saldo - cafetería", "quantity": 1,
                   "unit_price": float(recarga.total), "currency_id": "MXN"}],
        "external_reference": str(recarga.id),
        "notification_url": f"{base_url}/mp/aviso",
        "back_urls": {"success": regreso, "failure": regreso, "pending": regreso},
        "auto_return": "approved",
        "statement_descriptor": "CAFETERIA",
        # Aprobado o rechazado, nunca «pendiente»; sin medios que tardan en confirmarse.
        "binary_mode": True,
        "payment_methods": {"excluded_payment_types": [{"id": t} for t in EXCLUIDOS],
                            "installments": 1},
        "expires": True,
        "expiration_date_from": ahora.isoformat(timespec="milliseconds"),
        "expiration_date_to": recarga.liga_vence.isoformat(timespec="milliseconds"),
    }


def crear(db: Session, app: FastAPI, operador: Operador, tutor: Tutor, reparto: list[dict],
          total: Decimal, llave: str, base_url: str) -> Recarga:
    """La recarga y su preferencia. Con la misma llave (doble toque) devuelve la misma. La fila se
    confirma ANTES de llamar a MP: su id es el `external_reference` y no puede desaparecer."""
    r = db.scalar(select(Recarga).where(Recarga.operador_id == operador.id,
                                        Recarga.tutor_id == tutor.id, Recarga.llave == llave))
    if r is None:
        try:
            r = Recarga(operador_id=operador.id, tutor_id=tutor.id, llave=llave, total=total,
                        reparto=reparto)
            db.add(r)
            db.commit()
        except IntegrityError:
            # Doble envío simultáneo: otro worker la insertó primero; se usa la suya (Codex).
            db.rollback()
            r = db.scalar(select(Recarga).where(
                Recarga.operador_id == operador.id, Recarga.tutor_id == tutor.id,
                Recarga.llave == llave))
    if r.init_point:
        return r
    mp = cliente_mp(app, operador)
    if mp is None:
        raise ErrorMP(-1, "El operador no tiene MercadoPago configurado.")
    ahora = datetime.now(timezone.utc)
    r.liga_vence = ahora + VIGENCIA_PAGO
    pref = mp.crear_preferencia(_preferencia(r, base_url, ahora),
                                idempotencia=f"recarga-{operador.id}-{r.id}")
    r.mp_preferencia_id = str(pref.get("id") or "")[:100] or None
    # Siempre `init_point`: `sandbox_init_point` ya no existe en MP (lleva a una página de error).
    # Pruebas y producción sólo se distinguen por las credenciales.
    r.init_point = str(pref.get("init_point") or "")[:500] or None
    db.commit()
    if not r.init_point:
        raise ErrorMP(-1, "MercadoPago no devolvió la liga de pago.")
    return r


def _tomar(db: Session, operador: Operador, recarga_id: int,
          saltar: bool = False) -> Recarga | None:
    """La fila con candado. `saltar`: si otro la tiene, None en vez de esperar (barrido)."""
    return db.scalar(select(Recarga).where(Recarga.id == recarga_id,
                                           Recarga.operador_id == operador.id)
                     .with_for_update(skip_locked=saltar)
                     .execution_options(populate_existing=True))


def _importe(valor) -> Decimal | None:
    """Exacto, sin redondear: 149.999 no es 150 (Codex)."""
    if isinstance(valor, bool):
        return None
    try:
        d = Decimal(str(valor))
    except Exception:  # noqa: BLE001 — cualquier cosa rara: no cuadra
        return None
    return d if d.is_finite() else None


def procesar_pago(db: Session, app: FastAPI, operador: Operador, pago_id: str) -> Recarga | None:
    """Consulta el pago en MP y lo aplica. None si no es de una recarga de este operador.
    Lanza ErrorMP si MP no responde (el aviso devuelve 5xx y MP reintenta)."""
    mp = cliente_mp(app, operador)
    if mp is None:
        return None
    try:
        pago = mp.pago(pago_id)
    except ErrorMP as e:
        if e.status in (400, 404):
            return None
        raise
    return _aplicar(db, app, operador, pago)


def _aplicar(db: Session, app: FastAPI, operador: Operador, pago: dict) -> Recarga | None:
    ref = pago.get("external_reference")
    pid = str(pago.get("id") or "")
    if not isinstance(ref, str) or not _REFERENCIA.fullmatch(ref) or not pid:
        return None
    r = _tomar(db, operador, int(ref))
    if r is None:
        return None
    if pago.get("status") != "approved":
        if r.estado in ("pendiente", "vencida"):
            r.error = f"El pago {pid} no se aprobó ({pago.get('status')})."[:500]
            db.flush()
        return r
    if r.mp_pago_id and r.mp_pago_id != pid:
        # La misma preferencia se pagó otra vez: el dinero entró, pero la recarga ya tiene pago.
        # Se anota aparte, SIN tocar el estado: el reintento del primero sigue (Codex).
        if pid not in (r.pagos_extra or []):
            r.pagos_extra = [*(r.pagos_extra or []), pid]
            db.flush()
            log.warning("recarga %s: segundo pago aprobado %s", r.id, pid)
        return r
    if r.estado in ("acreditada", "revision"):
        return r
    if pago.get("currency_id") != "MXN" or _importe(pago.get("transaction_amount")) != r.total:
        r.mp_pago_id = pid
        r.estado = "revision"
        r.error = (f"El pago {pid} no cuadra: {pago.get('transaction_amount')} "
                   f"{pago.get('currency_id')} contra {r.total} MXN.")[:500]
        db.flush()
        log.warning("recarga %s: pago %s no cuadra", r.id, pid)
        return r
    r.mp_pago_id = pid
    r.estado = "aprobada"
    r.error = None
    db.flush()
    acreditar(db, app, operador, r)
    return r


def acreditar(db: Session, app: FastAPI, operador: Operador, recarga: Recarga) -> None:
    """Manda la recarga aprobada al TPV. Inciertos: sigue `aprobada` (la toma el barrido)."""
    if recarga.estado != "aprobada" or not recarga.mp_pago_id:
        return
    tutor = db.get(Tutor, recarga.tutor_id)
    cuerpo = {"tutor_ref": tutor.tutor_ref, "pago_ref": recarga.mp_pago_id,
              "total": float(recarga.total),
              "reparto": [{"alumno_id": p["alumno_id"], "importe": p["importe"]}
                          for p in recarga.reparto]}
    cliente = cliente_tutor(app, operador, app.state.settings.fernet_key)
    llave = f"mp:{recarga.mp_pago_id}"
    try:
        cliente.llamar("recarga", cuerpo)
    except ErrorTPV as e:
        registrar_escritura(db, operador.id, f"tutor:{tutor.id}", "recarga", llave, e)
        if es_incierto(e):
            recarga.error = "El TPV no respondió; se reintenta en un momento."
        else:
            # `pago_ya_acreditado` NO es éxito: el pago entró con otro reparto.
            recarga.estado = "revision"
            recarga.error = f"El TPV rechazó la recarga ({e.codigo}): {e.mensaje}"[:500]
            log.warning("recarga %s: el TPV la rechazó (%s)", recarga.id, e.codigo)
        db.flush()
        return
    registrar_escritura(db, operador.id, f"tutor:{tutor.id}", "recarga", llave, None)
    recarga.estado = "acreditada"
    recarga.error = None
    recarga.acreditada_en = datetime.now(timezone.utc)
    db.flush()


def revisar_pendiente(db: Session, app: FastAPI, operador: Operador, recarga: Recarga) -> Recarga:
    """Una recarga sin aviso: se busca su pago en MP. Sin pago tras 24 h, vence."""
    mp = cliente_mp(app, operador)
    if mp is None or recarga.estado not in ("pendiente", "vencida"):
        return recarga
    pagos = mp.buscar_por_referencia(str(recarga.id))
    aprobados = [p for p in pagos if p.get("status") == "approved"]
    # Todos los aprobados: el primero acredita, los demás quedan anotados como pagos extra.
    for p in (aprobados or pagos[:1]):
        _aplicar(db, app, operador, p)
        db.commit()
    # Vencer con la fila tomada: si entretanto otro camino la acreditó, no se pisa (Codex).
    r = _tomar(db, operador, recarga.id) or recarga
    if r.estado == "pendiente" and datetime.now(timezone.utc) - r.creada > VENCE_PENDIENTE:
        r.estado = "vencida"
        db.flush()
    return r


def conciliar(db: Session, app: FastAPI, operador: Operador, recarga_id: int) -> None:
    """Ya vencida la liga de pago, una última búsqueda de sus pagos: un pago de más cuyo aviso se
    perdió queda anotado en `pagos_extra` para la administración (Codex)."""
    mp = cliente_mp(app, operador)
    if mp is None:
        return
    for p in mp.buscar_por_referencia(str(recarga_id)):
        if p.get("status") == "approved":
            _aplicar(db, app, operador, p)
            db.commit()
    r = _tomar(db, operador, recarga_id)
    if r is not None:
        r.conciliada = True
        db.flush()


def reintentar(db: Session, app: FastAPI, operador: Operador, recarga_id: int,
               saltar: bool = False) -> Recarga | None:
    """Barrido y regreso del papá: con la fila tomada, acredita si sigue aprobada o busca el pago
    si sigue pendiente. `actualizada` marca el intento: el barrido toma primero las que llevan
    más tiempo sin intentarse."""
    r = _tomar(db, operador, recarga_id, saltar)
    if r is None:
        return None
    # Hora de la BD, como `creada`: comparar con la del proceso mezclaría dos relojes.
    r.actualizada = func.clock_timestamp()
    if r.estado == "aprobada":
        acreditar(db, app, operador, r)
    elif r.estado == "pendiente":
        db.commit()  # suelta el candado: `_aplicar` lo vuelve a tomar por pago
        revisar_pendiente(db, app, operador, r)
    return r
