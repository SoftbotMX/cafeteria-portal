"""Formato de dinero y fechas para las plantillas (español de México)."""
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

ZONA = ZoneInfo("America/Mexico_City")
DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

ESTADOS_PEDIDO = {
    "retenida": "Por recoger", "cobrada": "Entregado",
    "liberada": "No se recogió", "cancelada": "Cancelado",
}
TIPOS_CREDENCIAL = {"nfc": "Tarjeta", "qr": "Código QR"}


SIN_DATO = "—"


def _cantidad(x) -> Decimal | None:
    """None si el valor del TPV no es un número finito."""
    try:
        c = Decimal(str(x))
        if not c.is_finite():
            return None
        return c.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return None


def dinero(x) -> str:
    c = _cantidad(x)
    if c is None:
        return SIN_DATO
    return f"{'-' if c < 0 else ''}${abs(c):,.2f}"


def cambio(x) -> str:
    """Cambio de saldo con signo explícito: +$100.00, -$45.00. Vacío si es cero."""
    c = _cantidad(x)
    if c is None:
        return SIN_DATO
    if c == 0:
        return ""
    return ("+" if c > 0 else "-") + f"${abs(c):,.2f}"


def fecha(d) -> str:
    # El TPV entrega las fechas-hora en UTC: se pasan a la zona local antes de tomar el día.
    if isinstance(d, str) and len(d) > 10:
        try:
            d = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        except ValueError:
            return d
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        d = d.astimezone(ZONA).date()
    elif not isinstance(d, date):
        try:
            d = date.fromisoformat(str(d))
        except ValueError:
            return str(d)
    return f"{DIAS[d.weekday()]} {d.day:02d}/{d.month:02d}/{d.year}"


def registrar(templates) -> None:
    templates.env.filters.update(dinero=dinero, fecha=fecha, cambio=cambio)
    templates.env.globals.update(estados_pedido=ESTADOS_PEDIDO, tipos_credencial=TIPOS_CREDENCIAL)
