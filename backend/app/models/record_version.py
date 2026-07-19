import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class VersionReason(str, enum.Enum):
    initial_indexing = "initial_indexing"
    correction = "correction"  # indexer re-editing their own submission before the batch is completed
    rework_after_qa = "rework_after_qa"
    rework_after_customer_rejection = "rework_after_customer_rejection"


class RecordVersion(Base):
    __tablename__ = "record_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    indexed_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reason: Mapped[VersionReason] = mapped_column(Enum(VersionReason), nullable=False)

    record: Mapped["Record"] = relationship(back_populates="versions")
    author: Mapped["User"] = relationship(foreign_keys=[created_by])
