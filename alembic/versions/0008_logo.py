"""logo de la cafetería (multi-inquilino)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('operador', sa.Column('logo', sa.LargeBinary(), nullable=True))
    op.add_column('operador', sa.Column('logo_tipo', sa.String(30), nullable=True))
    op.add_column('operador', sa.Column('logo_version', sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column('operador', 'logo_version')
    op.drop_column('operador', 'logo_tipo')
    op.drop_column('operador', 'logo')
