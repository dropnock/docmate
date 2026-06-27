import enum

from sqlalchemy import Enum, Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class AQLStatus(str, enum.Enum):
    normal = "normal"
    tightened = "tightened"
    reduced = "reduced"


class AQLConfig(Base, TimestampMixin):
    __tablename__ = "aql_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), unique=True, nullable=False)
    current_status: Mapped[AQLStatus] = mapped_column(
        Enum(AQLStatus), default=AQLStatus.normal, nullable=False
    )
    consecutive_passes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    normal_aql: Mapped[float] = mapped_column(Float, default=1.5, nullable=False)
    tightened_aql: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    reduced_aql: Mapped[float] = mapped_column(Float, default=2.5, nullable=False)
    passes_to_reduce: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    failures_to_tighten: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    project: Mapped["Project"] = relationship(back_populates="aql_config")
