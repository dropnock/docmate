"""add aqlconfig.sampling_mode enum and lots.acceptance_number

Revision ID: d7e8f9a0b1c2
Revises: c3e4a5b6d7f8
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, None] = 'c3e4a5b6d7f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    sa.Enum("iso", "manual", name="samplingmode").create(op.get_bind(), checkfirst=True)
    sampling_mode_enum = PgEnum("iso", "manual", name="samplingmode", create_type=False)
    op.add_column(
        "aql_configs",
        sa.Column("sampling_mode", sampling_mode_enum, nullable=False, server_default="iso"),
    )
    op.alter_column("aql_configs", "sampling_mode", server_default=None)
    op.add_column("lots", sa.Column("acceptance_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("lots", "acceptance_number")
    op.drop_column("aql_configs", "sampling_mode")
    sa.Enum(name="samplingmode").drop(op.get_bind(), checkfirst=True)
