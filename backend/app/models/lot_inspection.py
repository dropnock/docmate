import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.qc_field_result import QcFieldStatus


class LotInspectionStatus(str, enum.Enum):
    in_progress = "in_progress"
    passed = "passed"
    failed = "failed"
    cancelled = "cancelled"


class InspectionItemStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    defective = "defective"


class LotInspection(Base, TimestampMixin):
    """The digitizing supervisor's outgoing ISO 2859-1 inspection of a draft
    lot, before it is released to the customer — distinct from the
    customer's own acceptance sampling (lot_service.apply_sample), which
    happens after release. One row per attempt: a failed inspection sends
    its defective records to rework and the lot is re-inspected with a
    fresh sample, so a lot can accumulate several rows. release_lot requires
    the latest non-cancelled attempt to have passed.

    Every sampling-plan input is frozen here (lot_size, aql_level,
    code_letter, acceptance/rejection numbers, random_seed) so the report
    shown to the customer can't drift if the lot, the project's AQL config
    or the lookup table change later, and so the random draw can be
    reproduced: random.Random(random_seed).sample(sorted lot record ids,
    sample_size) yields exactly the sampled records."""

    __tablename__ = "lot_inspections"
    __table_args__ = (UniqueConstraint("lot_id", "attempt_number", name="uq_lot_inspection_attempt"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    lot_id: Mapped[int] = mapped_column(Integer, ForeignKey("lots.id"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[LotInspectionStatus] = mapped_column(
        Enum(LotInspectionStatus), default=LotInspectionStatus.in_progress, nullable=False
    )
    aql_level: Mapped[float] = mapped_column(Float, nullable=False)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    code_letter: Mapped[str] = mapped_column(String(2), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    acceptance_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rejection_number: Mapped[int] = mapped_column(Integer, nullable=False)
    random_seed: Mapped[str] = mapped_column(String(64), nullable=False)
    # Populated by complete_inspection.
    critical_defect_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minor_defect_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    defective_record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_note: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    inspected_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lot = relationship("Lot")
    inspector = relationship("User")
    items = relationship(
        "LotInspectionItem", back_populates="inspection", cascade="all, delete-orphan",
        order_by="LotInspectionItem.id",
    )


class LotInspectionItem(Base, TimestampMixin):
    """One sampled record within an inspection. document_type_id and
    record_version are snapshotted at sampling time: a defective record is
    requeued for indexing when the inspection completes, which detaches it
    from its batch (the only path back to its document type) and bumps its
    version, but the report must keep describing the data as inspected."""

    __tablename__ = "lot_inspection_items"
    __table_args__ = (UniqueConstraint("inspection_id", "record_id", name="uq_lot_inspection_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(Integer, ForeignKey("lot_inspections.id"), nullable=False, index=True)
    record_id: Mapped[int] = mapped_column(Integer, ForeignKey("records.id"), nullable=False, index=True)
    document_type_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("document_types.id"), nullable=True)
    record_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[InspectionItemStatus] = mapped_column(
        Enum(InspectionItemStatus), default=InspectionItemStatus.pending, nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    inspection = relationship("LotInspection", back_populates="items")
    record = relationship("Record")
    field_results = relationship(
        "LotInspectionFieldResult", back_populates="item", cascade="all, delete-orphan",
        order_by="LotInspectionFieldResult.id",
    )


class LotInspectionFieldResult(Base, TimestampMixin):
    """Per-field mark on an inspected record — same shape and critical-flag
    snapshotting as QcFieldResult (see its docstring), kept separate because
    QcFieldResult is tied to a customer QC Task and this review has none."""

    __tablename__ = "lot_inspection_field_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(Integer, ForeignKey("lot_inspection_items.id"), nullable=False, index=True)
    field_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[QcFieldStatus] = mapped_column(Enum(QcFieldStatus), nullable=False)
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    item = relationship("LotInspectionItem", back_populates="field_results")
