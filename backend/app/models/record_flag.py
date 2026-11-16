import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FlagStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"    # a supervisor reviewed it and recorded how
    withdrawn = "withdrawn"  # the indexer took the flag down themselves


class RecordFlag(Base):
    """An issue an indexer escalates to a supervisor on one record (see
    flag_service). At most one open flag per record; resolving or
    withdrawing it closes it but keeps the row, so the record's history
    still shows what was raised and how it was settled. Flagging never
    moves the record — it stays in its batch whatever the outcome."""

    __tablename__ = "record_flags"
    __table_args__ = (Index("ix_record_flags_project_status", "project_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=FlagStatus.open.value)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raised_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set while a digitizing supervisor has shared the flag with the
    # project's customer supervisors (flag_service.share_flags); cleared again
    # if they stop sharing it. Shared flags carry a discussion (FlagComment).
    shared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shared_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class FlagComment(Base):
    """One message in a shared flag's discussion between the digitizing and
    customer supervisors. Immutable once written."""

    __tablename__ = "flag_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    flag_id: Mapped[int] = mapped_column(ForeignKey("record_flags.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
