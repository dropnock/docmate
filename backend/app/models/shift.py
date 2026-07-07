import enum
from datetime import time

from sqlalchemy import Enum, ForeignKey, String, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class ShiftRole(str, enum.Enum):
    indexer = "indexer"
    qa = "qa"


class Shift(Base, TimestampMixin):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="shifts")
    project_shifts: Mapped[list["ProjectShift"]] = relationship(back_populates="shift")
    user_assignments: Mapped[list["UserProjectAssignment"]] = relationship(back_populates="shift")


class ProjectShift(Base):
    __tablename__ = "project_shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), nullable=False)

    project: Mapped["Project"] = relationship(back_populates="shifts")
    shift: Mapped["Shift"] = relationship(back_populates="project_shifts")


class UserProjectAssignment(Base, TimestampMixin):
    __tablename__ = "user_project_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), nullable=False)
    shift_role: Mapped[ShiftRole | None] = mapped_column(Enum(ShiftRole), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    user: Mapped["User"] = relationship(back_populates="project_assignments")
    project: Mapped["Project"] = relationship(back_populates="staff")
    shift: Mapped["Shift"] = relationship(back_populates="user_assignments")
