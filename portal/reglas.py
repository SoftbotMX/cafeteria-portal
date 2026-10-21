"""Reglas de cuándo se puede pedir y cancelar. Puras: reciben `ahora_utc` (datetime con zona)."""
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from portal.models import ConfigPedidos, DiaSinServicio

ZONA_MX = "America/Mexico_City"
ZONAS = ["America/Mexico_City", "America/Monterrey", "America/Matamoros", "America/Chihuahua",
         "America/Hermosillo", "America/Mazatlan", "America/Tijuana", "America/Cancun"]


@dataclass(frozen=True)
class Reglas:
    hora_limite: time | None
    dias_servicio: frozenset[int]
    zona: ZoneInfo
    anticipacion: int
    sin_servicio: frozenset[date]


# Sin configuración guardada: sin hora límite, todos los días, 14 días de anticipación.
SIN_CONFIG = Reglas(None, frozenset(range(7)), ZoneInfo(ZONA_MX), 14, frozenset())


def cargar_reglas(db: Session, operador_id: int) -> Reglas:
    cfg = db.scalars(select(ConfigPedidos).where(ConfigPedidos.operador_id == operador_id)).first()
    fechas = frozenset(db.scalars(
        select(DiaSinServicio.fecha).where(DiaSinServicio.operador_id == operador_id)))
    if cfg is None:
        # Los días sin servicio ya capturados valen aunque falte el resto de la configuración.
        return replace(SIN_CONFIG, sin_servicio=fechas)
    return Reglas(
        hora_limite=cfg.hora_limite,
        dias_servicio=frozenset(int(d) for d in cfg.dias_servicio),
        zona=ZoneInfo(cfg.zona_horaria),
        anticipacion=cfg.dias_anticipacion,
        sin_servicio=fechas,
    )


def ahora_local(reglas: Reglas, ahora_utc: datetime) -> datetime:
    return ahora_utc.astimezone(reglas.zona)


def _paso_limite(reglas: Reglas, local: datetime) -> bool:
    # Igual a la hora límite ya no cuenta: sólo antes.
    return reglas.hora_limite is not None and local.time() >= reglas.hora_limite


def motivo_fecha_invalida(reglas: Reglas, fecha: date, ahora_utc: datetime) -> str | None:
    local = ahora_local(reglas, ahora_utc)
    hoy = local.date()
    if fecha < hoy:
        return "Esa fecha ya pasó."
    if fecha == hoy and _paso_limite(reglas, local):
        return f"Ya pasó la hora límite para pedir para hoy ({reglas.hora_limite:%H:%M})."
    if fecha.weekday() not in reglas.dias_servicio or fecha in reglas.sin_servicio:
        return "Ese día no hay servicio."
    if fecha > hoy + timedelta(days=reglas.anticipacion):
        return f"Sólo se puede pedir con {reglas.anticipacion} días de anticipación."
    return None


def fechas_disponibles(reglas: Reglas, ahora_utc: datetime) -> list[date]:
    hoy = ahora_local(reglas, ahora_utc).date()
    dias = (hoy + timedelta(days=i) for i in range(reglas.anticipacion + 1))
    return [f for f in dias if motivo_fecha_invalida(reglas, f, ahora_utc) is None]


def puede_cancelar(reglas: Reglas, fecha_entrega: date, ahora_utc: datetime) -> bool:
    local = ahora_local(reglas, ahora_utc)
    hoy = local.date()
    if fecha_entrega < hoy:
        return False
    if fecha_entrega == hoy:
        return not _paso_limite(reglas, local)
    return True
