"""nombre del padre de familia por partes (nombre(s), apellido 1, apellido 2)

Los que ya existen se separan con la regla del TPV: las dos últimas palabras son los apellidos.
La administración los corrige desde la ficha.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def _partir(nombre: str):
    p = (nombre or "").split()
    if len(p) >= 3:
        return " ".join(p[:-2]), p[-2], p[-1]
    if len(p) == 2:
        return p[0], p[1], None
    return (p[0] if p else ""), None, None


def upgrade() -> None:
    op.add_column('tutor', sa.Column('nombres', sa.String(100), nullable=True))
    op.add_column('tutor', sa.Column('apellido_1', sa.String(100), nullable=True))
    op.add_column('tutor', sa.Column('apellido_2', sa.String(100), nullable=True))
    con = op.get_bind()
    tutor = sa.table('tutor', sa.column('id', sa.Integer), sa.column('nombre', sa.String),
                     sa.column('nombres', sa.String), sa.column('apellido_1', sa.String),
                     sa.column('apellido_2', sa.String))
    for id_, nombre in con.execute(sa.select(tutor.c.id, tutor.c.nombre)).all():
        n, a1, a2 = _partir(nombre)
        con.execute(tutor.update().where(tutor.c.id == id_)
                    .values(nombres=n[:100] or None, apellido_1=a1 and a1[:100],
                            apellido_2=a2 and a2[:100]))


def downgrade() -> None:
    op.drop_column('tutor', 'apellido_2')
    op.drop_column('tutor', 'apellido_1')
    op.drop_column('tutor', 'nombres')
