import pytest

from portal.formato import apellidos_de, dinero_partes, nombre_corto, nombres_de
from portal.models import Tutor
from portal.nombres import juntar, partir


@pytest.mark.parametrize("nombre, partes", [
    ("Laura Méndez Ruiz", ("Laura", "Méndez", "Ruiz")),
    ("Ana Karen Treviño Soto", ("Ana Karen", "Treviño", "Soto")),
    ("Ana López", ("Ana", "López", None)),
    ("Cher", ("Cher", None, None)),
    ("  ", ("", None, None)),
])
def test_partir_usa_la_regla_del_tpv(nombre, partes):
    assert partir(nombre) == partes


def test_juntar_omite_lo_vacio():
    assert juntar("Ana", "López", None) == "Ana López"
    assert juntar("Ana", " ", "") == "Ana"


def test_filtros_de_alumno_prefieren_las_partes_del_tpv():
    ficha = {"nombre": "Ana Karen Pérez Soto", "nombres": "Ana Karen",
             "apellido_paterno": "Pérez", "apellido_materno": "Soto"}
    assert nombres_de(ficha) == "Ana Karen"
    assert apellidos_de(ficha) == "Pérez Soto"
    assert nombre_corto(ficha) == "Ana Karen Pérez"


def test_filtros_de_alumno_sin_partes_separan_el_nombre():
    ficha = {"nombre": "Sofía Méndez Ruiz"}
    assert (nombres_de(ficha), apellidos_de(ficha), nombre_corto(ficha)) == (
        "Sofía", "Méndez Ruiz", "Sofía Méndez")


def test_dinero_partes():
    assert dinero_partes("1186.5") == ("$", "1,186", ".50")
    assert dinero_partes(-3) == ("-$", "3", ".00")
    assert dinero_partes("x") == ("", "—", "")


def test_saludo_usa_nombres_si_hay_partes():
    assert Tutor(nombre="Laura Méndez Ruiz", nombres="Laura").nombre_pila == "Laura"
    assert Tutor(nombre="Ana Karen Treviño Soto").nombre_pila == "Ana Karen"
