"""pagos en línea: pausa independiente del token y datos de la cuenta de Mercado Pago

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('operador', sa.Column('mp_pagos_activos', sa.Boolean(), nullable=False,
                                        server_default=sa.text('true')))
    op.add_column('operador', sa.Column('mp_cuenta_id', sa.String(30), nullable=True))
    op.add_column('operador', sa.Column('mp_cuenta_nombre', sa.String(200), nullable=True))
    op.add_column('operador', sa.Column('mp_cuenta_prueba', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('operador', 'mp_cuenta_prueba')
    op.drop_column('operador', 'mp_cuenta_nombre')
    op.drop_column('operador', 'mp_cuenta_id')
    op.drop_column('operador', 'mp_pagos_activos')
