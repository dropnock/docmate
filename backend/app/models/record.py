import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class RecordStatus(str, enum.Enum):
    pending = "pending"
    indexing = "indexing"
    indexed = "indexed"
    qa_pending = "qa_pending"
    qa_passed = "qa_passed"
    qa_failed = "qa_failed"
    qc_pending = "qc_pending"
    qc_passed = "qc_passed"
    qc_failed = "qc_failed"


class Record(Base, TimestampMixin):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    file_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    indexed_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    locked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[RecordStatus] = mapped_column(
        Enum(RecordStatus), default=RecordStatus.pending, nullable=False
    )

    batch: Mapped["Batch"] = relationship(back_populates="records")
    locker: Mapped["User | None"] = relationship(foreign_keys=[locked_by])
    versions: Mapped[list["RecordVersion"]] = relationship(
        back_populates="record", order_by="RecordVersion.version_number"
    )
    tasks: Mapped[list["Task"]] = relationship(back_populates="record")
