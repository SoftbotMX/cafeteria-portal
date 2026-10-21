from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from portal.reglas import (
    SIN_CONFIG, Reglas, ahora_local, fechas_disponibles, motivo_fecha_invalida, puede_cancelar,
)

MX = ZoneInfo("America/Mexico_City")  # UTC-6 todo el año desde 2022


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def reglas(**kw):
    base = dict(hora_limite=time(9, 0), dias_servicio=frozenset(range(5)), zona=MX,
                anticipacion=14, sin_servicio=frozenset())
    base.update(kw)
    return Reglas(**base)


# 2026-10-21 es miércoles.
MIE = date(2026, 10, 21)


def test_sin_config_todo_valido():
    ahora = utc(2026, 10, 24, 20, 0)  # sábado
    assert motivo_fecha_invalida(SIN_CONFIG, date(2026, 10, 24), ahora) is None  # hoy, sin límite
    assert motivo_fecha_invalida(SIN_CONFIG, date(2026, 10, 25), ahora) is None  # domingo
    assert motivo_fecha_invalida(SIN_CONFIG, date(2026, 11, 7), ahora) is None   # +14
    assert motivo_fecha_invalida(SIN_CONFIG, date(2026, 11, 8), ahora) is not None


def test_borde_hora_limite():
    r = reglas()
    assert motivo_fecha_invalida(r, MIE, utc(2026, 10, 21, 14, 59, 59)) is None
    m = motivo_fecha_invalida(r, MIE, utc(2026, 10, 21, 15, 0, 0))
    assert m == "Ya pasó la hora límite para pedir para hoy (09:00)."


def test_hoy_es_el_dia_local():
    r = reglas()
    ahora = utc(2026, 10, 22, 5, 30)  # 23:30 del 21 en México
    assert ahora_local(r, ahora).date() == MIE
    assert motivo_fecha_invalida(r, MIE, ahora) == "Ya pasó la hora límite para pedir para hoy (09:00)."
    assert motivo_fecha_invalida(r, date(2026, 10, 22), ahora) is None
    assert motivo_fecha_invalida(r, date(2026, 10, 20), ahora) == "Esa fecha ya pasó."


def test_dia_sin_servicio_y_fin_de_semana():
    r = reglas(sin_servicio=frozenset({date(2026, 10, 23)}))
    ahora = utc(2026, 10, 21, 12, 0)
    assert motivo_fecha_invalida(r, date(2026, 10, 24), ahora) == "Ese día no hay servicio."  # sábado
    assert motivo_fecha_invalida(r, date(2026, 10, 23), ahora) == "Ese día no hay servicio."  # feriado
    assert motivo_fecha_invalida(r, date(2026, 10, 22), ahora) is None


def test_anticipacion():
    r = reglas(anticipacion=3, dias_servicio=frozenset(range(7)))
    ahora = utc(2026, 10, 21, 12, 0)
    assert motivo_fecha_invalida(r, date(2026, 10, 24), ahora) is None
    assert motivo_fecha_invalida(r, date(2026, 10, 25), ahora) == \
        "Sólo se puede pedir con 3 días de anticipación."


def test_fechas_disponibles_salta_hoy_tras_limite():
    r = reglas(anticipacion=5, sin_servicio=frozenset({date(2026, 10, 22)}))
    antes = fechas_disponibles(r, utc(2026, 10, 21, 14, 0))
    assert antes == [MIE, date(2026, 10, 23), date(2026, 10, 26)]  # jue 22 sin servicio; sáb y dom fuera
    despues = fechas_disponibles(r, utc(2026, 10, 21, 15, 0))
    assert despues == [date(2026, 10, 23), date(2026, 10, 26)]
    assert fechas_disponibles(SIN_CONFIG, utc(2026, 10, 21, 15, 0))[0] == MIE
    assert len(fechas_disponibles(SIN_CONFIG, utc(2026, 10, 21, 15, 0))) == 15


def test_puede_cancelar():
    r = reglas()
    assert puede_cancelar(r, date(2026, 10, 20), utc(2026, 10, 21, 12, 0)) is False
    assert puede_cancelar(r, MIE, utc(2026, 10, 21, 14, 59)) is True
    assert puede_cancelar(r, MIE, utc(2026, 10, 21, 15, 0)) is False
    assert puede_cancelar(r, date(2026, 10, 22), utc(2026, 10, 21, 20, 0)) is True
    assert puede_cancelar(SIN_CONFIG, MIE, utc(2026, 10, 21, 23, 0)) is True


def test_hora_ingenua_se_rechaza():
    from datetime import datetime, date
    import pytest
    from portal.reglas import SIN_CONFIG, motivo_fecha_invalida, puede_cancelar
    ingenua = datetime(2026, 10, 21, 12, 0)
    with pytest.raises(ValueError):
        motivo_fecha_invalida(SIN_CONFIG, date(2026, 10, 22), ingenua)
    with pytest.raises(ValueError):
        puede_cancelar(SIN_CONFIG, date(2026, 10, 22), ingenua)


def test_ahora_utc_es_aware():
    from portal.reglas import ahora_utc
    assert ahora_utc().utcoffset().total_seconds() == 0
