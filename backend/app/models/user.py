import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class UserRole(str, enum.Enum):
    admin = "admin"
    de_supervisor = "de_supervisor"
    de_staff = "de_staff"
    de_indexer = "de_indexer"      # deprecated: collapsed into de_staff, kept only because Postgres enum values can't be dropped
    de_qa_agent = "de_qa_agent"    # deprecated: collapsed into de_staff, kept only because Postgres enum values can't be dropped
    customer_supervisor = "customer_supervisor"
    customer_qc_agent = "customer_qc_agent"


class Portal(str, enum.Enum):
    digitizing = "digitizing"
    customer = "customer"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    keycloak_sub: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    portal: Mapped[Portal] = mapped_column(Enum(Portal), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Presence (services/presence_service.py): the last authenticated request,
    # and when the current run of activity began.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    session_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    organization: Mapped["Organization | None"] = relationship(back_populates="users")
    project_assignments: Mapped[list["UserProjectAssignment"]] = relationship(back_populates="user")
