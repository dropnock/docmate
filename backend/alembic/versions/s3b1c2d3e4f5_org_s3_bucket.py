"""org s3 bucket — add s3_bucket_name and s3_bucket_status to organizations

Revision ID: s3b1c2d3e4f5
Revises: kc1a2b3c4d5e
Create Date: 2026-06-27 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

revision = "s3b1c2d3e4f5"
down_revision = "kc1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    orgbucketstatus = PgEnum(
        "provisioning", "ready", "error",
        name="orgbucketstatus",
        create_type=False,
    )
    op.execute("CREATE TYPE orgbucketstatus AS ENUM ('provisioning', 'ready', 'error')")
    op.add_column(
        "organizations",
        sa.Column("s3_bucket_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("s3_bucket_status", orgbucketstatus, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "s3_bucket_status")
    op.drop_column("organizations", "s3_bucket_name")
    op.execute("DROP TYPE orgbucketstatus")
