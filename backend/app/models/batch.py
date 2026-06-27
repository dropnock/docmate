import enum

from sqlalchemy import Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class BatchStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    indexing = "indexing"
    qa_review = "qa_review"
    customer_qc = "customer_qc"
    passed = "passed"
    rejected = "rejected"


class Batch(Base, TimestampMixin):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    document_type_id: Mapped[int] = mapped_column(ForeignKey("document_types.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[BatchStatus] = mapped_column(
        Enum(BatchStatus), default=BatchStatus.draft, nullable=False
    )
    aql_level_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    aql_sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="batches")
    document_type: Mapped["DocumentType"] = relationship(back_populates="batches")
    records: Mapped[list["Record"]] = relationship(back_populates="batch")
    tasks: Mapped[list["Task"]] = relationship(back_populates="batch")
    qc_result: Mapped["BatchQCResult | None"] = relationship(back_populates="batch", uselist=False)


class BatchQCResult(Base, TimestampMixin):
    __tablename__ = "batch_qc_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), unique=True, nullable=False)
    total_inspected: Mapped[int] = mapped_column(Integer, nullable=False)
    defects_found: Mapped[int] = mapped_column(Integer, nullable=False)
    acceptance_number: Mapped[int] = mapped_column(Integer, nullable=False)
    aql_level_applied: Mapped[float] = mapped_column(Float, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)  # passed | rejected

    batch: Mapped["Batch"] = relationship(back_populates="qc_result")
