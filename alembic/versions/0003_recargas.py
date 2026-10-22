"""recargas con MercadoPago

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('operador', sa.Column('mp_access_token_cif', sa.String(), nullable=True))
    op.create_table(
        'recarga',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('operador_id', sa.Integer(), nullable=False),
        sa.Column('tutor_id', sa.Integer(), nullable=False),
        sa.Column('llave', sa.String(length=120), nullable=False),
        sa.Column('total', sa.Numeric(10, 2), nullable=False),
        sa.Column('reparto', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('estado', sa.String(length=12), server_default='pendiente', nullable=False),
        sa.Column('mp_preferencia_id', sa.String(length=100), nullable=True),
        sa.Column('init_point', sa.String(length=500), nullable=True),
        sa.Column('liga_vence', sa.DateTime(timezone=True), nullable=True),
        sa.Column('mp_pago_id', sa.String(length=50), nullable=True),
        sa.Column('pagos_extra', postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('error', sa.String(length=500), nullable=True),
        sa.Column('conciliada', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('creada', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('actualizada', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('acreditada_en', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['operador_id'], ['operador.id']),
        sa.ForeignKeyConstraint(['tutor_id'], ['tutor.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('operador_id', 'tutor_id', 'llave', name='uq_recarga_llave'),
    )
    op.create_index('uq_recarga_operador_pago', 'recarga', ['operador_id', 'mp_pago_id'],
                    unique=True, postgresql_where=sa.text('mp_pago_id IS NOT NULL'))
    op.create_index('ix_recarga_estado', 'recarga', ['estado'])


def downgrade() -> None:
    op.drop_index('ix_recarga_estado', table_name='recarga')
    op.drop_index('uq_recarga_operador_pago', table_name='recarga')
    op.drop_table('recarga')
    op.drop_column('operador', 'mp_access_token_cif')
