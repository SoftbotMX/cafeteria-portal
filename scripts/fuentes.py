"""Genera los íconos y las fuentes recortadas que sirve el portal (portal/estatico/).

Sólo se corre al agregar un ícono o cambiar de fuente; el resultado se versiona. Necesita la
dependencia de desarrollo `fonttools` (`pip install -e '.[dev]'`).

    .venv/bin/python scripts/fuentes.py \\
        --iconos ../softbot-ui/src/assets/fonts/icons.woff2 \\
        --roboto-flex 'RobotoFlex[GRAD,XOPQ,...,wdth,wght].ttf' --roboto 'Roboto[wdth,wght].ttf'

Las dos fuentes variables se bajan de github.com/google/fonts (ofl/robotoflex y ofl/roboto).

- Íconos: Material Symbols de softbot-ui, con el estilo del diseño (sin relleno, grosor 400,
  tamaño óptico 24), como sprite SVG: `{{ icono("check") }}` en las plantillas.
- Roboto Flex (títulos y cifras): sólo condensada (ancho 50 a 70 %), grosores 300 a 800 y
  tamaño óptico 14 a 96 (el navegador lo ajusta al tamaño del texto: en grande, las letras se
  angostan; fijarlo en 14 hace los títulos casi tan anchos como Roboto).
- Roboto (texto): ancho normal, grosores 400 a 800.
Ambas fuentes, sólo con los caracteres del español y la puntuación que usa el portal.
"""
import argparse
from pathlib import Path

from fontTools import subset
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

# Nombres de Material Symbols. Para agregar uno: escríbelo aquí y vuelve a correr el script.
ICONOS = sorted({
    "add", "add_card", "arrow_back", "arrow_forward", "check", "chevron_left", "chevron_right",
    "close", "content_copy", "credit_card", "error", "expand_less", "expand_more", "group",
    "hourglass_top", "info", "link_off", "logout", "notifications_active", "open_in_new",
    "payments", "person_add", "qr_code_2", "receipt_long", "remove", "schedule", "storefront",
    "swap_horiz", "swap_vert", "warning",
})

# Latín básico, Latín-1 (acentos, ñ, ¿¡, «»), comillas, guiones, viñeta, puntos suspensivos y −.
CARACTERES = "U+0020-007E,U+00A0-00FF,U+2013-2014,U+2018-201E,U+2022,U+2026,U+2212"

RAIZ = Path(__file__).resolve().parent.parent
ESTATICO = RAIZ / "portal" / "estatico"


def sprite_iconos(origen: Path, destino: Path) -> None:
    fuente = instancer.instantiateVariableFont(
        TTFont(origen), {"FILL": 0, "GRAD": 0, "opsz": 24, "wght": 400})
    glifos = fuente.getGlyphSet()
    lado = fuente["head"].unitsPerEm  # Material Symbols dibuja cada ícono en un cuadro de 960
    faltan = [n for n in ICONOS if n not in glifos]
    if faltan:
        raise SystemExit(f"No existen en la fuente: {', '.join(faltan)}")
    simbolos = []
    for nombre in ICONOS:
        pluma = SVGPathPen(glifos)
        # La fuente mide hacia arriba y el SVG hacia abajo: se voltea el eje y.
        glifos[nombre].draw(TransformPen(pluma, (1, 0, 0, -1, 0, lado)))
        simbolos.append(f'<symbol id="i-{nombre}" viewBox="0 0 {lado} {lado}">'
                        f'<path d="{pluma.getCommands()}"/></symbol>')
    destino.write_text('<svg xmlns="http://www.w3.org/2000/svg">' + "".join(simbolos) + "</svg>\n")


def recortar(origen: Path, destino: Path, ejes: dict) -> None:
    # Primero los caracteres y luego los ejes: al revés, el instanciador de fontTools tropieza
    # con glifos que la fuente declara pero no dibuja (p. ej. el guion suave de Roboto Flex).
    fuente = TTFont(origen)
    opciones = subset.Options()
    opciones.layout_features = ["kern", "liga", "tnum", "lnum"]
    opciones.name_IDs = ["*"]  # conserva el aviso de licencia
    opciones.notdef_outline = True
    sub = subset.Subsetter(opciones)
    sub.populate(unicodes=subset.parse_unicodes(CARACTERES))
    sub.subset(fuente)
    fijos = {a.axisTag: a.defaultValue for a in fuente["fvar"].axes}
    fijos.update(ejes)  # los ejes que no se nombran quedan en su valor por omisión
    fuente = instancer.instantiateVariableFont(fuente, fijos)
    fuente.flavor = "woff2"
    fuente.save(destino)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--iconos", type=Path, required=True, help="icons.woff2 de softbot-ui")
    p.add_argument("--roboto-flex", type=Path, required=True, help="RobotoFlex.ttf (variable)")
    p.add_argument("--roboto", type=Path, required=True, help="Roboto[wdth,wght].ttf (variable)")
    a = p.parse_args()

    (ESTATICO / "fuentes").mkdir(exist_ok=True)
    salidas = {
        ESTATICO / "iconos.svg": lambda d: sprite_iconos(a.iconos, d),
        ESTATICO / "fuentes" / "roboto-flex.woff2": lambda d: recortar(
            a.roboto_flex, d, {"wdth": (50, 70), "wght": (300, 800), "opsz": (14, 96)}),
        ESTATICO / "fuentes" / "roboto.woff2": lambda d: recortar(
            a.roboto, d, {"wdth": 100, "wght": (400, 800)}),
    }
    for destino, hacer in salidas.items():
        hacer(destino)
        print(f"{destino.relative_to(RAIZ)}: {destino.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
