"""bot de WhatsApp

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('operador', sa.Column('bot_escrituras', sa.Boolean(),
                                        server_default=sa.text('true'), nullable=False))
    op.create_table(
        'bot_conversacion',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('operador_id', sa.Integer(), nullable=False),
        sa.Column('telefono', sa.String(length=10), nullable=False),
        sa.Column('datos', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('actualizada', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['operador_id'], ['operador.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('operador_id', 'telefono', name='uq_bot_conversacion'),
    )


    op.create_table(
        'bot_mensaje',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('operador_id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.String(length=100), nullable=False),
        sa.Column('respuesta', sa.String(), nullable=True),
        sa.Column('creado', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['operador_id'], ['operador.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('operador_id', 'message_id', name='uq_bot_mensaje'),
    )


def downgrade() -> None:
    op.drop_table('bot_mensaje')
    op.drop_table('bot_conversacion')
    op.drop_column('operador', 'bot_escrituras')
