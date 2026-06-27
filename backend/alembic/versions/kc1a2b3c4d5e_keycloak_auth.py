"""keycloak auth — add keycloak_sub to users, realm_slug to orgs

Revision ID: kc1a2b3c4d5e
Revises: 4lequxy65bsv
Create Date: 2026-06-27 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "kc1a2b3c4d5e"
down_revision = "4lequxy65bsv"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users: add keycloak_sub, make hashed_password nullable
    op.add_column("users", sa.Column("keycloak_sub", sa.String(255), nullable=True))
    op.alter_column("users", "hashed_password", existing_type=sa.String(255), nullable=True)
    op.create_index(
        "ix_users_keycloak_sub",
        "users",
        ["keycloak_sub"],
        unique=True,
        postgresql_where=sa.text("keycloak_sub IS NOT NULL"),
    )

    # organizations: add realm_slug
    op.add_column("organizations", sa.Column("realm_slug", sa.String(100), nullable=True))
    op.create_index(
        "ix_organizations_realm_slug",
        "organizations",
        ["realm_slug"],
        unique=True,
        postgresql_where=sa.text("realm_slug IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_organizations_realm_slug", table_name="organizations")
    op.drop_column("organizations", "realm_slug")
    op.drop_index("ix_users_keycloak_sub", table_name="users")
    op.alter_column("users", "hashed_password", existing_type=sa.String(255), nullable=False)
    op.drop_column("users", "keycloak_sub")
