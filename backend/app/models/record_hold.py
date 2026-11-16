from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RecordHold(Base):
    """A digitizing supervisor holds an unassigned record back so it isn't
    put into an indexing batch (see hold_service). At most one active hold
    per record — released_at is null while it's active. Cancelling a hold
    keeps the row, so the record's history shows who held it, why, and who
    let it go."""

    __tablename__ = "record_holds"
    __table_args__ = (
        Index("ix_record_holds_project_active", "project_id", "released_at"),
        Index(
            "uq_record_holds_one_active", "record_id", unique=True,
            postgresql_where=text("released_at IS NULL"), sqlite_where=text("released_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    held_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    held_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    released_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_note: Mapped[str | None] = mapped_column(Text, nullable=True)
