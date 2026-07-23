from datetime import datetime
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


class CompleteTaskRequest(BaseModel):
    indexed_data: dict | None = None
