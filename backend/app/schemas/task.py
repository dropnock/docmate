from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AssignTaskRequest(BaseModel):
    record_id: int
    batch_id: int
    task_type: str  # indexing | qa | qc
    agent_id: int


class BulkReassignRequest(BaseModel):
    task_ids: list[int]
    agent_id: int


class TaskOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    record_id: int
    batch_id: int
    task_type: str
    assigned_to: int | None
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    processing_time_seconds: int | None
    # Only populated by GET /tasks/mine (see that endpoint) — lets the
    # frontend tell an open indexing batch's tasks apart from everything
    # else without a second request. Absent (null) elsewhere, same pattern
    # as BatchOut.indexer_name.
    batch_status: str | None = None


class StartTaskRequest(BaseModel):
    pass


class FieldResultIn(BaseModel):
    field_key: str
    status: Literal["accepted", "defective"]
    note: str | None = None


class CompleteTaskRequest(BaseModel):
    indexed_data: dict | None = None
    # Only meaningful for task_type=qc on a project in AQLConfig.
    # sampling_mode="iso" — see task_service.complete_task. Ignored
    # otherwise (manual mode, or non-QC tasks).
    field_results: list[FieldResultIn] | None = None


class FailTaskRequest(BaseModel):
    reason: str
    field_results: list[FieldResultIn] | None = None
