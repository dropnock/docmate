"""lot inspection level: normal / tightened / reduced chosen per lot

Revision ID: 7c1e9a4d2b6f
Revises: 2be2522001ac
Create Date: 2026-11-19 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

revision: str = '7c1e9a4d2b6f'
down_revision: Union[str, None] = '2be2522001ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# aqlstatus already exists (aql_configs.current_status, initial schema).
aqlstatus = PgEnum("normal", "tightened", "reduced", name="aqlstatus", create_type=False)


def upgrade() -> None:
    # Inspections drawn before this change all came from a single table, so
    # they are recorded as normal inspection.
    op.add_column(
        "lot_inspections",
        sa.Column("inspection_level", aqlstatus, nullable=False, server_default="normal"),
    )
    op.add_column("lots", sa.Column("inspection_level", aqlstatus, nullable=True))
    op.execute(
        "UPDATE lots SET inspection_level = 'normal' "
        "WHERE id IN (SELECT lot_id FROM lot_inspections)"
    )


def downgrade() -> None:
    op.drop_column("lots", "inspection_level")
    op.drop_column("lot_inspections", "inspection_level")
