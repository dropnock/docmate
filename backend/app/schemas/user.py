import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, field_validator

# Accepts any syntactically valid email including internal domains (.local, .internal, etc.)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", re.IGNORECASE)
EmailLike = Annotated[str, ...]

_RETIRED_ROLES = {"de_indexer", "de_qa_agent"}


def _reject_retired_role(v: str | None) -> str | None:
    if v in _RETIRED_ROLES:
        raise ValueError(f"'{v}' has been retired — use 'de_staff' instead")
    return v


class UserCreate(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address")
        return v
    temp_password: str
    full_name: str
    role: str       # UserRole value
    portal: str     # Portal value
    organization_id: int | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        return _reject_retired_role(v)


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    organization_id: int | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str | None) -> str | None:
        return _reject_retired_role(v)


class UserOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    tenant_id: int
    email: str
    full_name: str
    role: str
    portal: str
    is_active: bool
    organization_id: int | None


class OnlineTask(BaseModel):
    id: int
    task_type: str
    record_id: int
    record_name: str
    batch_name: str
    project_name: str
    last_active_at: datetime | None
    working: bool


class OnlineUser(BaseModel):
    id: int
    full_name: str
    email: str
    role: str
    portal: str
    organization_name: str | None
    last_seen_at: datetime
    session_started_at: datetime | None
    status: str  # active | idle
    task: OnlineTask | None
