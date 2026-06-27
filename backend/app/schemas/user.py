from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    email: EmailStr
    password: str
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
