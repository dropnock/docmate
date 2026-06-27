from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class DocumentType(Base, TimestampMixin):
    __tablename__ = "document_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    json_schema: Mapped[dict] = mapped_column(JSON, nullable=False)

    project: Mapped["Project"] = relationship(back_populates="document_types")
    batches: Mapped[list["Batch"]] = relationship(back_populates="document_type")
