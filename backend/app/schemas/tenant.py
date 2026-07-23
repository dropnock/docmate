from datetime import date
from pydantic import BaseModel


class TenantCreate(BaseModel):
    name: str
    slug: str


class TenantOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    name: str
    slug: str


class OrgCreate(BaseModel):
    name: str
    type: str  # "digitizing_entity" | "customer"


class OrgOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    tenant_id: int
    name: str
    type: str
    realm_slug: str | None = None
    s3_bucket_name: str | None = None
    s3_bucket_status: str | None = None


class ProjectCreate(BaseModel):
    customer_org_id: int
    name: str
    description: str | None = None
    proposed_end_date: date | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    proposed_end_date: date | None = None


class ProjectOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    tenant_id: int
    digitizing_org_id: int
    customer_org_id: int
    name: str
    description: str | None
    proposed_end_date: date | None
    s3_bucket_name: str | None
    s3_bucket_status: str
