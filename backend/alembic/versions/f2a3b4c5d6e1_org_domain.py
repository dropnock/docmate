"""organizations: add domain column

Revision ID: f2a3b4c5d6e1
Revises: e1f2a3b4c5d6
Create Date: 2026-07-03

"""
from alembic import op
import sqlalchemy as sa

revision = "f2a3b4c5d6e1"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("domain", sa.String(255), nullable=True),
    )
    op.create_index("ix_organizations_domain", "organizations", ["domain"])


def downgrade() -> None:
    op.drop_index("ix_organizations_domain", table_name="organizations")
    op.drop_column("organizations", "domain")
