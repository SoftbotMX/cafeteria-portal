"""Marca de cada instalación (logo y nombre de la cafetería) e íconos del rediseño."""
import re
from pathlib import Path

import pytest

from portal.web import icono
from tests.fabricas import crear_operador

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
PLANTILLAS = Path(__file__).resolve().parent.parent / "portal" / "plantillas"


def test_logo_sin_subir_da_404(client, db):
    crear_operador(db)
    assert client.get("/logo").status_code == 404


def test_logo_se_sirve_con_su_tipo_y_cache_por_version(client, db):
    crear_operador(db, logo=PNG, logo_tipo="image/png", logo_version="abc123")
    r = client.get("/logo?v=abc123")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"]
    assert r.headers["x-content-type-options"] == "nosniff"
    # Con otra versión (o sin ella) no se guarda: al cambiar el logo se ve el nuevo.
    assert client.get("/logo?v=vieja").headers["cache-control"] == "no-cache"


def test_logo_es_de_la_cafeteria_del_host(client, db):
    crear_operador(db, host="otra.test", logo=PNG, logo_tipo="image/png", logo_version="x")
    crear_operador(db)  # cafeteria.test, sin logo
    assert client.get("/logo").status_code == 404


def test_entrar_muestra_nombre_y_logo_de_la_cafeteria(client, db):
    crear_operador(db, nombre="Colegio Renuevo", logo=PNG, logo_tipo="image/png",
                   logo_version="v1")
    r = client.get("/entrar")
    assert "Colegio Renuevo" in r.text
    assert 'src="/logo?v=v1"' in r.text
    assert "Funciona con Cafetería by Softbot" in r.text


def test_entrar_sin_logo_muestra_solo_el_nombre(client, db):
    crear_operador(db, nombre="Colegio Renuevo")
    r = client.get("/entrar")
    assert "Colegio Renuevo" in r.text and "/logo" not in r.text


def test_icono_desconocido_falla():
    with pytest.raises(ValueError):
        icono("no_existe")


def test_icono_es_decorativo():
    html = str(icono("check"))
    assert 'aria-hidden="true"' in html and "#i-check" in html


def test_todos_los_iconos_de_las_plantillas_existen():
    """Un nombre mal escrito en una plantilla que las pruebas no recorren también se detecta."""
    usados = set()
    for archivo in PLANTILLAS.rglob("*.html"):
        usados |= set(re.findall(r'icono\(\s*"([a-z0-9_]+)"', archivo.read_text()))
    assert usados, "no se encontró ningún ícono"
    for nombre in sorted(usados):
        icono(nombre)
