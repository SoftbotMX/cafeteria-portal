"""Formato de dinero y fechas para las plantillas (español de México)."""
import re
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from portal.nombres import de_alumno, juntar

ZONA = ZoneInfo("America/Mexico_City")
DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

ESTADOS_PEDIDO = {
    "retenida": "Por recoger", "cobrada": "Entregado",
    "liberada": "No se recogió", "cancelada": "Cancelado",
}
TIPOS_CREDENCIAL = {"nfc": "Tarjeta", "qr": "Código QR"}

# `tipo` REAL de un movimiento del TPV (sb.cafe.movimiento.TIPOS) -> texto para el papá. La
# `descripcion` que manda el TPV es la etiqueta interna del tipo («Retención», «Cargo»…), pensada
# para la cafetería, no para quien paga. Qué es cada uno según cómo lo escribe el TPV:
#   recarga        saldo que entra (MercadoPago o caja).
#   retencion      aparta saldo: un pedido hecho con fecha, y también cada compra en caja (que se
#                  aparta y cobra al instante). No siempre es un pedido: «Saldo apartado».
#   cargo          consume lo apartado: compra o pedido entregado.
#   liberacion     devuelve lo apartado: pedido cancelado o no recogido, o la parte no consumida.
#   transferencia  entre hijos del mismo tutor; con el signo se sabe si salió o llegó.
#   ajuste         corrección de la gerencia, siempre con motivo.
ETIQUETAS_MOVIMIENTO = {
    "recarga": "Recarga",
    "retencion": "Saldo apartado",
    "cargo": "Compra",
    "liberacion": "Saldo liberado",
    "transferencia": "Transferencia",
    "ajuste": "Ajuste de la cafetería",
}


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


# `150`, `150.5`, `150.50`, `$1,500.00`: signo de pesos y comas de millar opcionales, hasta 2
# decimales, 9 enteros como tope (un error de dedo no mueve millones).
_IMPORTE = re.compile(r"\$?\s*((?:[0-9]{1,3}(?:,[0-9]{3}){1,2}|[0-9]{1,9}))(\.[0-9]{1,2})?")


def parse_importe(texto: str | None) -> Decimal | None:
    """Lo que escribió el tutor como `Decimal` de EXACTAMENTE 2 decimales, o None si no es un
    importe válido (vacío, texto, ≤ 0, más de 2 decimales: no se redondea en silencio)."""
    m = _IMPORTE.fullmatch((texto or "").strip())
    if m is None:
        return None
    importe = Decimal(m.group(1).replace(",", "") + (m.group(2) or "")).quantize(Decimal("0.01"))
    return importe if importe > 0 else None


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


def etiqueta_movimiento(m) -> str:
    """Texto de un movimiento para el papá; un `tipo` desconocido cae a la `descripcion` del TPV."""
    if not isinstance(m, dict):
        return "Movimiento"
    tipo = m.get("tipo")
    etiqueta = ETIQUETAS_MOVIMIENTO.get(tipo) if isinstance(tipo, str) else None
    if etiqueta is None:
        return str(m.get("descripcion") or "Movimiento")
    if tipo == "transferencia":
        c = _cantidad(m.get("disponible"))
        if c is not None and c != 0:
            return "Transferencia recibida" if c > 0 else "Transferencia enviada"
    return etiqueta


def fecha(d, zona: ZoneInfo | None = None) -> str:
    # El TPV entrega las fechas-hora en UTC: se pasan a la zona local antes de tomar el día.
    if isinstance(d, str) and len(d) > 10:
        try:
            d = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        except ValueError:
            return d
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        d = d.astimezone(zona or ZONA).date()
    elif not isinstance(d, date):
        try:
            d = date.fromisoformat(str(d))
        except ValueError:
            return str(d)
    return f"{DIAS[d.weekday()]} {d.day:02d}/{d.month:02d}/{d.year}"


def dinero_partes(x) -> tuple[str, str, str]:
    """Para la cifra grande: («$», «1,186», «.50»); con signo si es negativo (« -$»)."""
    c = _cantidad(x)
    if c is None:
        return ("", SIN_DATO, "")
    entero, centavos = f"{abs(c):,.2f}".split(".")
    return ("-$" if c < 0 else "$", entero, "." + centavos)


def nombres_de(alumno) -> str:
    return de_alumno(alumno)[0] if isinstance(alumno, dict) else ""


def apellidos_de(alumno) -> str:
    if not isinstance(alumno, dict):
        return ""
    _, a1, a2 = de_alumno(alumno)
    return juntar("", a1, a2)


def nombre_corto(alumno) -> str:
    """Nombre(s) y apellido 1 («Sofía Méndez»): el de las tarjetas de inicio."""
    if not isinstance(alumno, dict):
        return ""
    n, a1, _ = de_alumno(alumno)
    return juntar(n, a1, None) or str(alumno.get("nombre") or "")


def registrar(templates) -> None:
    templates.env.filters.update(dinero=dinero, fecha=fecha, cambio=cambio,
                                 movimiento=etiqueta_movimiento, dinero_partes=dinero_partes,
                                 nombres_de=nombres_de, apellidos_de=apellidos_de,
                                 nombre_corto=nombre_corto)
    templates.env.globals.update(estados_pedido=ESTADOS_PEDIDO, tipos_credencial=TIPOS_CREDENCIAL)
