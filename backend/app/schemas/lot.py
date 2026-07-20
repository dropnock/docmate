from datetime import datetime

from pydantic import BaseModel, Field


class LotCreate(BaseModel):
    project_id: int
    name: str
    description: str | None = None
    record_ids: list[int]


class LotOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    project_id: int
    name: str
    description: str | None
    status: str
    sample_rate: float | None
    sample_size: int | None
    accuracy_rate: float | None
    released_at: datetime | None = None
    released_by: int | None
    created_by: int | None


class ApplySampleRequest(BaseModel):
    sample_rate: float


class QcBatchAssignment(BaseModel):
    agent_id: int
    record_ids: list[int] = Field(min_length=1)


class CreateQcBatchesRequest(BaseModel):
    project_id: int
    document_type_id: int
    assignments: list[QcBatchAssignment] = Field(min_length=1)


class RemediationRequest(BaseModel):
    pass
