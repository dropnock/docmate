import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AuditEntityType(str, enum.Enum):
    record = "record"
    task = "task"
    batch = "batch"
    project = "project"
    user = "user"


class AuditAction(str, enum.Enum):
    created = "created"
    status_changed = "status_changed"
    locked = "locked"
    unlocked = "unlocked"
    lock_expired = "lock_expired"
    assigned = "assigned"
    reassigned = "reassigned"
    sampled = "sampled"
    indexing_submitted = "indexing_submitted"
    version_created = "version_created"
    qa_passed = "qa_passed"
    qa_failed = "qa_failed"
    qc_passed = "qc_passed"
    qc_rejected = "qc_rejected"
    batch_escalated = "batch_escalated"
    stale_flagged = "stale_flagged"
    deactivated = "deactivated"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    entity_type: Mapped[AuditEntityType] = mapped_column(Enum(AuditEntityType), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    action: Mapped[AuditAction] = mapped_column(Enum(AuditAction), nullable=False)
    performed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    old_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    actor: Mapped["User | None"] = relationship(foreign_keys=[performed_by])
