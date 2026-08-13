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
    acceptance_number: int | None
    accuracy_rate: float | None
    released_at: datetime | None = None
    released_by: int | None
    created_by: int | None


class ApplySampleRequest(BaseModel):
    # Required when the project's AQLConfig.sampling_mode is "manual", must
    # be omitted when "iso" (the sample size is computed instead) — see
    # lot_service.apply_sample for the validation rule.
    sample_rate: float | None = None


class QcBatchAssignment(BaseModel):
    agent_id: int
    record_ids: list[int] = Field(min_length=1)


class CreateQcBatchesRequest(BaseModel):
    project_id: int
    document_type_id: int
    assignments: list[QcBatchAssignment] = Field(min_length=1)


class RemediationRequest(BaseModel):
    pass
