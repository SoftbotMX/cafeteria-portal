"""Nombre por partes: nombre(s), apellido 1 y apellido 2.

Cuando sólo hay el nombre completo se usa la misma regla que el TPV (docs/API_TPV.md,
`admin/alumno/crear`): las dos últimas palabras son los apellidos. Con dos palabras, la segunda
es el apellido 1; con una, sólo hay nombre(s).
"""


def partir(nombre: str) -> tuple[str, str | None, str | None]:
    p = (nombre or "").split()
    if len(p) >= 3:
        return " ".join(p[:-2]), p[-2], p[-1]
    if len(p) == 2:
        return p[0], p[1], None
    return (p[0] if p else ""), None, None


def juntar(nombres: str, apellido_1: str | None, apellido_2: str | None) -> str:
    return " ".join(x.strip() for x in (nombres, apellido_1, apellido_2) if x and x.strip())


def de_alumno(ficha: dict) -> tuple[str, str | None, str | None]:
    """Partes de una ficha del TPV: las suyas si las manda, si no, separadas del nombre."""
    nombres = ficha.get("nombres")
    if isinstance(nombres, str) and nombres.strip():
        a1, a2 = ficha.get("apellido_paterno"), ficha.get("apellido_materno")
        return (nombres.strip(), a1.strip() if isinstance(a1, str) and a1.strip() else None,
                a2.strip() if isinstance(a2, str) and a2.strip() else None)
    return partir(ficha.get("nombre") or "")
