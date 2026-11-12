import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.aql import AQLStatus
from app.models.base import Base, TimestampMixin


class LotStatus(str, enum.Enum):
    draft = "draft"
    released = "released"
    qc_in_progress = "qc_in_progress"
    passed = "passed"
    failed = "failed"
    remediation = "remediation"


class Lot(Base, TimestampMixin):
    __tablename__ = "lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[LotStatus] = mapped_column(default=LotStatus.draft, nullable=False)
    sample_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Only populated when the lot was sampled under AQLConfig.sampling_mode
    # == "iso" — the ISO 2859-1 acceptance number that goes with sample_size,
    # same field name/meaning as BatchQCResult.acceptance_number.
    acceptance_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accuracy_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Snapshot of AQLConfig.current_status (normal/tightened/reduced) at the
    # moment this lot was sampled (see apply_sample) — the real ISO 2859-1
    # inspection-level concept for reporting, frozen so it can't drift if the
    # project's live status later changes. Set for both iso and manual mode.
    aql_status_snapshot: Mapped[AQLStatus | None] = mapped_column(Enum(AQLStatus), nullable=True)
    # Set by calculate_accuracy the moment it finalizes lot.status to
    # passed/failed — NOT derivable from updated_at, which bumps on every
    # later write (e.g. send_for_remediation) and would silently drift.
    qc_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Only populated for iso-mode lots (calculate_accuracy) — no per-field
    # QcFieldResult data exists for manual-mode lots to derive these from.
    critical_defect_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minor_defect_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Which customer QC round the live sample/result fields above describe.
    # Starts at 1; resubmit_lot archives a failed round into LotQcRound and
    # bumps this. QcFieldResult rows carry the round they were marked in.
    qc_round: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    project = relationship("Project", backref="lots")
    releaser = relationship("User", foreign_keys=[released_by])
    creator = relationship("User", foreign_keys=[created_by])
    lot_records = relationship("LotRecord", back_populates="lot", cascade="all, delete-orphan")


class LotRecord(Base):
    __tablename__ = "lot_records"
    __table_args__ = (
        UniqueConstraint("lot_id", "record_id", name="uq_lot_record"),
        # A record is in at most one lot (lot_service.create_lot checks first).
        UniqueConstraint("record_id", name="uq_lot_records_record_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(Integer, ForeignKey("lots.id"), nullable=False, index=True)
    record_id: Mapped[int] = mapped_column(Integer, ForeignKey("records.id"), nullable=False, index=True)
    is_sampled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    lot = relationship("Lot", back_populates="lot_records")
    record = relationship("Record", back_populates="lot_records")


class LotQcRound(Base, TimestampMixin):
    """A finished customer QC round of a lot that was later resubmitted —
    the lot's live sample/result fields at the moment resubmit_lot reset
    them, frozen so earlier rounds stay reportable. The current round lives
    on the Lot row itself."""
    __tablename__ = "lot_qc_rounds"
    __table_args__ = (UniqueConstraint("lot_id", "round_number", name="uq_lot_qc_round"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    lot_id: Mapped[int] = mapped_column(Integer, ForeignKey("lots.id"), nullable=False, index=True)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[LotStatus] = mapped_column(Enum(LotStatus), nullable=False)  # the round's verdict
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    acceptance_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accuracy_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    aql_status_snapshot: Mapped[AQLStatus | None] = mapped_column(Enum(AQLStatus), nullable=True)
    critical_defect_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minor_defect_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sampled_record_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    qc_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resubmitted_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
