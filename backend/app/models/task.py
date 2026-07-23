import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class TaskType(str, enum.Enum):
    indexing = "indexing"
    qa = "qa"
    qc = "qc"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id"), nullable=False, index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType), nullable=False)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), default=TaskStatus.pending, nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    record: Mapped["Record"] = relationship(back_populates="tasks")
    batch: Mapped["Batch"] = relationship(back_populates="tasks")
    assignee: Mapped["User | None"] = relationship(foreign_keys=[assigned_to])
    assigner: Mapped["User | None"] = relationship(foreign_keys=[assigned_by])
