import re
from typing import Annotated

from pydantic import BaseModel, field_validator

# Accepts any syntactically valid email including internal domains (.local, .internal, etc.)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", re.IGNORECASE)
EmailLike = Annotated[str, ...]


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


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    organization_id: int | None = None


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
