"""servidor MCP

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('operador', sa.Column('mcp_token_hash', sa.String(length=64), nullable=True))
    op.create_table(
        'mcp_confirmacion',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('folio', sa.String(length=32), nullable=False),
        sa.Column('operador_id', sa.Integer(), nullable=False),
        sa.Column('tutor_id', sa.Integer(), nullable=False),
        sa.Column('accion', sa.String(length=20), nullable=False),
        sa.Column('datos', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('llave', sa.String(length=64), nullable=False),
        sa.Column('expira', sa.DateTime(timezone=True), nullable=False),
        sa.Column('estado', sa.String(length=10), server_default='preparada', nullable=False),
        sa.Column('resultado', sa.String(), nullable=True),
        sa.Column('creada', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['operador_id'], ['operador.id']),
        sa.ForeignKeyConstraint(['tutor_id'], ['tutor.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('folio', name='uq_mcp_confirmacion_folio'),
    )


def downgrade() -> None:
    op.drop_table('mcp_confirmacion')
    op.drop_column('operador', 'mcp_token_hash')
