from datetime import datetime

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.task import FieldResultIn


class LotCreate(BaseModel):
    project_id: int
    name: str
    description: str | None = None
    record_ids: list[int] = Field(min_length=1)


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
    aql_status_snapshot: str | None = None
    qc_completed_at: datetime | None = None
    critical_defect_count: int | None = None
    minor_defect_count: int | None = None
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


class ContributingAgent(BaseModel):
    id: int
    full_name: str


class FieldResultSummary(BaseModel):
    field_key: str
    is_critical: bool
    defective_count: int
    accepted_count: int
    contributing_agents: list[ContributingAgent]


class QcFieldResultsOut(BaseModel):
    lot_id: int
    any_critical_defect: bool
    non_critical_defect_total: int
    acceptance_number: int | None
    fields: list[FieldResultSummary]


class StartInspectionRequest(BaseModel):
    # Must be one of aql_service.AVAILABLE_AQLS — validated there.
    aql_level: float


class ReviewInspectionItemRequest(BaseModel):
    field_results: list[FieldResultIn]


class CompleteInspectionRequest(BaseModel):
    summary_note: str | None = Field(default=None, max_length=2048)


class RecallLotRequest(BaseModel):
    """Why the lot is being taken back. Recorded on the audit trail, which
    the customer can see once the lot is released again."""
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]


class RemoveLotRecordsRequest(BaseModel):
    record_ids: list[int] = Field(min_length=1)
