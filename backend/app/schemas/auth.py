from pydantic import BaseModel


class MeResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    email: str
    full_name: str
    role: str
    portal: str
    is_active: bool
    organization_id: int | None


class RealmLookupResponse(BaseModel):
    realm_slug: str
