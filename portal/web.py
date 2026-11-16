import hashlib
import re
from functools import cache
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from portal.formato import registrar
from portal.nombres import partir

ESTATICO = Path(__file__).parent / "estatico"


@cache
def _huella(ruta: str) -> str:
    return hashlib.sha256((ESTATICO / ruta).read_bytes()).hexdigest()[:10]


def estatico(ruta: str) -> str:
    """URL de un archivo de /estatico con su huella: al cambiar el archivo cambia la URL."""
    return f"/estatico/{ruta}?v={_huella(ruta)}"


@cache
def _iconos() -> frozenset[str]:
    return frozenset(re.findall(r'id="i-([a-z0-9_]+)"', (ESTATICO / "iconos.svg").read_text()))


def icono(nombre: str, clase: str = "") -> Markup:
    """Ícono del sprite (Material Symbols de softbot-ui). Decorativo: el texto va al lado."""
    if nombre not in _iconos():
        # Un nombre mal escrito rompe la página en las pruebas, no en el celular del papá.
        raise ValueError(f"Ícono desconocido: {nombre!r} (agrégalo en scripts/fuentes.py)")
    clases = f"icono {clase}".strip()
    return Markup('<svg class="{}" aria-hidden="true" focusable="false"><use href="{}#i-{}"/></svg>'
                  ).format(escape(clases), estatico("iconos.svg"), nombre)


templates = Jinja2Templates(directory=str(Path(__file__).parent / "plantillas"))
registrar(templates)
templates.env.globals.update(icono=icono, estatico=estatico, partir=partir)
