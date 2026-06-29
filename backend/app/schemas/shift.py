from datetime import time
from pydantic import BaseModel


class ShiftCreate(BaseModel):
    name: str
    start_time: time
    end_time: time
    timezone: str = "UTC"


class ShiftOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    tenant_id: int
    name: str
    start_time: time
    end_time: time
    timezone: str


class ShiftUpdate(BaseModel):
    name: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    timezone: str | None = None


class AssignShiftToProject(BaseModel):
    shift_id: int


class AssignStaffToProject(BaseModel):
    user_id: int
    shift_id: int


class StaffAssignmentOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    user_id: int
    project_id: int
    shift_id: int
    is_active: bool


class AvailableStaffOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    full_name: str
    email: str
    role: str
