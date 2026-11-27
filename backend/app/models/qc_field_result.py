import enum

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.defect import ErrorType, Severity


class QcFieldStatus(str, enum.Enum):
    accepted = "accepted"
    defective = "defective"


class QcFieldResult(Base, TimestampMixin):
    """One row per top-level schema field a QC agent marked while reviewing
    a sampled record, only recorded when the project's AQLConfig.
    sampling_mode is "iso" — see task_service.complete_task/fail_task.

    lot_id is denormalized here (reachable via task->record->lot_record->lot)
    so the supervisor tabulation view (lot_service.get_field_result_
    tabulation) can GROUP BY lot_id/field_key in one query instead of a
    multi-table join. is_critical is snapshotted from the document type's
    schema at write time rather than joined live, so a later edit to a
    field's "x-critical" flag (CabinetManager's schema editor allows this at
    any time) doesn't retroactively change the criticality of marks a QC
    agent already made earlier in the same lot's review."""

    __tablename__ = "qc_field_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    record_id: Mapped[int] = mapped_column(Integer, ForeignKey("records.id"), nullable=False, index=True)
    lot_id: Mapped[int] = mapped_column(Integer, ForeignKey("lots.id"), nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    field_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[QcFieldStatus] = mapped_column(Enum(QcFieldStatus), nullable=False)
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Error type the reviewer chose and the class it was given
    # (defect_class_service.classify), both frozen at marking time so a later
    # change to the agreement or the schema doesn't change past verdicts.
    # Accepted fields have neither; marks made before error types existed
    # have a severity but no error type.
    error_type: Mapped[ErrorType | None] = mapped_column(
        Enum(ErrorType, native_enum=False, length=20, validate_strings=True), nullable=True,
    )
    severity: Mapped[Severity | None] = mapped_column(
        Enum(Severity, native_enum=False, length=10, validate_strings=True), nullable=True,
    )
    # The lot's customer QC round this mark belongs to (Lot.qc_round at
    # write time) — tabulation and the lot verdict only count the current one.
    qc_round: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    # The customer QC supervisor's correction of this mark while the lot
    # awaits their decision (lot_service.override_qc_mark). The reviewer's
    # own mark above is never changed; counts and the verdict use the
    # override when there is one (defect_class_service.effective).
    override_status: Mapped[QcFieldStatus | None] = mapped_column(
        Enum(QcFieldStatus, native_enum=False, length=10, validate_strings=True), nullable=True,
    )
    override_error_type: Mapped[ErrorType | None] = mapped_column(
        Enum(ErrorType, native_enum=False, length=20, validate_strings=True), nullable=True,
    )
    override_severity: Mapped[Severity | None] = mapped_column(
        Enum(Severity, native_enum=False, length=10, validate_strings=True), nullable=True,
    )
    override_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    overridden_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task = relationship("Task")
    record = relationship("Record")
    lot = relationship("Lot")
