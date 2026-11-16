"""Reglas de cuándo se puede pedir y cancelar. Puras: reciben `ahora_utc` (datetime con zona)."""
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
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


def ahora_utc() -> datetime:
    """La hora de las reglas. Las rutas la piden como dependencia: las pruebas la sustituyen."""
    return datetime.now(timezone.utc)


def ahora_fresca(request) -> datetime:
    """La hora de ESTE instante, respetando la sustitución de `ahora_utc` que hagan las pruebas.
    Una escritura la pide justo antes de validar: la dependencia se resolvió antes de las
    lecturas al TPV, que pueden tardar, y la hora límite no puede quedarse atrás."""
    return request.app.dependency_overrides.get(ahora_utc, ahora_utc)()


def _exigir_aware(ahora: datetime) -> None:
    # Una hora sin zona se tomaría como local del servidor y movería la hora límite en silencio.
    if ahora.tzinfo is None or ahora.utcoffset() is None:
        raise ValueError("ahora_utc debe traer zona horaria")


def ahora_local(reglas: Reglas, ahora_utc: datetime) -> datetime:
    _exigir_aware(ahora_utc)
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


MESES_CORTOS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
DIAS_CORTOS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


@dataclass(frozen=True)
class DiaPedido:
    fecha: date
    hoy: bool
    motivo: str | None  # None = se puede pedir

    @property
    def corto(self) -> str:
        """«LUN»… o «HOY»."""
        return "HOY" if self.hoy else DIAS_CORTOS[self.fecha.weekday()].upper()

    @property
    def dia_mes(self) -> str:
        return f"{self.fecha.day} {MESES_CORTOS[self.fecha.month - 1]}"


@dataclass(frozen=True)
class SemanaPedido:
    inicio: date              # lunes
    dias: tuple[DiaPedido, ...]
    actual: bool = False      # la semana de hoy

    @property
    def etiqueta(self) -> str:
        """«del 11 al 15 nov» o «del 25 nov al 1 dic» (del primer al último día que se muestra)."""
        a, b = self.dias[0].fecha, self.dias[-1].fecha
        if a.month == b.month:
            return f"del {a.day} al {b.day} {MESES_CORTOS[b.month - 1]}"
        return f"del {a.day} {MESES_CORTOS[a.month - 1]} al {b.day} {MESES_CORTOS[b.month - 1]}"


def semanas_para_pedir(reglas: Reglas, ahora_utc: datetime,
                       motivos: dict[date, str] | None = None) -> list[SemanaPedido]:
    """Semanas (de lunes) desde la de hoy hasta la del último día que se puede pedir. Columnas:
    lunes a viernes, y sábado o domingo sólo si la cafetería da servicio ese día. Cada día dice
    por qué no se puede (la misma regla que valida el pedido); `motivos` agrega el motivo que
    capturó la administración a los días sin servicio."""
    hoy = ahora_local(reglas, ahora_utc).date()
    ultimo = hoy + timedelta(days=reglas.anticipacion)
    columnas = [d for d in range(7) if d < 5 or d in reglas.dias_servicio]
    semanas = []
    lunes = hoy - timedelta(days=hoy.weekday())
    while lunes <= ultimo:
        dias = []
        for d in columnas:
            f = lunes + timedelta(days=d)
            motivo = motivo_fecha_invalida(reglas, f, ahora_utc)
            if motivo == "Ese día no hay servicio." and (motivos or {}).get(f):
                motivo = f"Sin servicio: {motivos[f]}"
            dias.append(DiaPedido(f, f == hoy, motivo))
        # Una semana sin ningún día disponible (p. ej. hoy es sábado sin servicio) no se ofrece.
        if any(d.motivo is None for d in dias):
            semanas.append(SemanaPedido(lunes, tuple(dias), lunes <= hoy < lunes + timedelta(days=7)))
        lunes += timedelta(days=7)
    return semanas


def motivos_sin_servicio(db: Session, operador_id: int) -> dict[date, str]:
    return {f: m for f, m in db.execute(
        select(DiaSinServicio.fecha, DiaSinServicio.motivo)
        .where(DiaSinServicio.operador_id == operador_id)) if m}
