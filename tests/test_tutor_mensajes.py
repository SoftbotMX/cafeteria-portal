"""Textos por operación y parámetros obligatorios de los helpers de escritura del tutor."""
import inspect

import pytest

from portal import tutor_tpv

OPERACIONES = ["precompra", "precompra/cancelar", "transferencia", "alumno/permiso",
               "credencial/bloquear", "credencial/desbloquear"]


def test_cada_operacion_tiene_su_texto():
    assert len({tutor_tpv.msg_llave(o) for o in OPERACIONES}) == len(OPERACIONES)
    assert len({tutor_tpv.msg_cambio(o) for o in OPERACIONES}) == len(OPERACIONES)


def test_cancelar_no_dice_armar_el_pedido():
    assert "armar" not in tutor_tpv.msg_cambio("precompra/cancelar")
    assert "pedido" not in tutor_tpv.msg_llave("alumno/permiso").lower()
    assert "pedido" not in tutor_tpv.msg_llave("credencial/bloquear").lower()


@pytest.mark.parametrize("f", [tutor_tpv.ya_registrado, tutor_tpv.intento_incierto,
                               tutor_tpv.con_aviso])
def test_operacion_es_obligatoria(f):
    assert inspect.signature(f).parameters["operacion"].default is inspect.Parameter.empty
