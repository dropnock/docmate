import enum
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class S3BucketStatus(str, enum.Enum):
    provisioning = "provisioning"
    ready = "ready"
    error = "error"


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    digitizing_org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    customer_org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    proposed_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    s3_bucket_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    s3_bucket_status: Mapped[S3BucketStatus] = mapped_column(
        Enum(S3BucketStatus), default=S3BucketStatus.provisioning, nullable=False
    )
    tenant: Mapped["Tenant"] = relationship(back_populates="projects")
    digitizing_org: Mapped["Organization"] = relationship(foreign_keys=[digitizing_org_id])
    customer_org: Mapped["Organization"] = relationship(foreign_keys=[customer_org_id])
    shifts: Mapped[list["ProjectShift"]] = relationship(back_populates="project")
    staff: Mapped[list["UserProjectAssignment"]] = relationship(back_populates="project")
    document_types: Mapped[list["DocumentType"]] = relationship(back_populates="project")
    batches: Mapped[list["Batch"]] = relationship(back_populates="project")
    aql_config: Mapped["AQLConfig | None"] = relationship(back_populates="project", uselist=False)
