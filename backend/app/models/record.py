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
    disqualified = "disqualified"  # legacy — superseded by withdrawn/ineligible below, kept for existing rows
    withdrawn = "withdrawn"
    ineligible = "ineligible"
    excluded = "excluded"


# A record in any of these statuses was skipped rather than indexed (see
# task_service.skip_task) — terminal, never routed through QA/QC. Batch
# progress checks (batch_service.complete_indexing_batch/_maybe_complete_batch,
# batch_service.auto_advance_to_qa) treat these the same as "indexed"/
# "qa_passed" respectively so one skipped record never blocks a batch.
SKIPPED_RECORD_STATUSES = frozenset({
    RecordStatus.disqualified,
    RecordStatus.withdrawn,
    RecordStatus.ineligible,
    RecordStatus.excluded,
})


class Record(Base, TimestampMixin):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id"), nullable=True, index=True)
    cabinet_id: Mapped[int | None] = mapped_column(ForeignKey("cabinets.id"), nullable=True, index=True)
    file_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_identifier: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    indexed_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    locked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[RecordStatus] = mapped_column(
        Enum(RecordStatus), default=RecordStatus.pending, nullable=False
    )

    batch: Mapped["Batch | None"] = relationship(back_populates="records")
    cabinet: Mapped["Cabinet | None"] = relationship(back_populates="records")
    locker: Mapped["User | None"] = relationship(foreign_keys=[locked_by])
    versions: Mapped[list["RecordVersion"]] = relationship(
        back_populates="record", order_by="RecordVersion.version_number"
    )
    tasks: Mapped[list["Task"]] = relationship(back_populates="record")
    lot_records: Mapped[list["LotRecord"]] = relationship(back_populates="record")
