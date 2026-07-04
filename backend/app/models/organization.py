import enum

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class OrgType(str, enum.Enum):
    digitizing_entity = "digitizing_entity"
    customer = "customer"


class OrgBucketStatus(str, enum.Enum):
    provisioning = "provisioning"
    ready = "ready"
    error = "error"


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[OrgType] = mapped_column(Enum(OrgType), nullable=False)
    realm_slug: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    s3_bucket_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    s3_bucket_status: Mapped[OrgBucketStatus | None] = mapped_column(
        Enum(OrgBucketStatus), nullable=True
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="organizations")
    users: Mapped[list["User"]] = relationship(back_populates="organization")
