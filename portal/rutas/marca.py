"""Logo de la cafetería: cada instalación muestra el suyo (lo sube la administración)."""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from portal.db import get_db
from portal.models import Operador
from portal.sesiones import operador_actual

router = APIRouter()


@router.get("/logo")
def logo(v: str = "", operador: Operador = Depends(operador_actual),
         db: Session = Depends(get_db)):
    op = db.scalar(select(Operador).options(undefer(Operador.logo))
                   .where(Operador.id == operador.id))
    if not op.logo or not op.logo_tipo:
        raise HTTPException(404)
    # Con la versión vigente en la URL se guarda un año: al subir otro logo cambia la versión.
    cache = ("public, max-age=31536000, immutable" if v and v == op.logo_version
             else "no-cache")
    return Response(op.logo, media_type=op.logo_tipo,
                    headers={"Cache-Control": cache, "ETag": f'"{op.logo_version}"',
                             "X-Content-Type-Options": "nosniff"})
