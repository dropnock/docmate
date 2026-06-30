"""cabinet organization link

Revision ID: a1b2c3d4e5f6
Revises: 9992d5b0bd2d
Create Date: 2026-06-30 00:00:00.000000

"""
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9992d5b0bd2d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'cabinets',
        sa.Column('organization_id', sa.Integer(), nullable=True)
    )
    op.create_index('ix_cabinets_organization_id', 'cabinets', ['organization_id'])
    op.create_foreign_key(
        'fk_cabinets_organization_id',
        'cabinets', 'organizations',
        ['organization_id'], ['id']
    )


def downgrade() -> None:
    op.drop_constraint('fk_cabinets_organization_id', 'cabinets', type_='foreignkey')
    op.drop_index('ix_cabinets_organization_id', table_name='cabinets')
    op.drop_column('cabinets', 'organization_id')
