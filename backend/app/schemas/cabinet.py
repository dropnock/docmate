from pydantic import BaseModel, Field


class CabinetCreate(BaseModel):
    project_id: int
    organization_id: int | None = None
    name: str
    description: str | None = None


class CabinetOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    project_id: int
    organization_id: int | None
    name: str
    description: str | None
    created_by: int | None
    created_at: str | None = None

    @classmethod
    def from_orm_dt(cls, obj):
        return cls(
            id=obj.id,
            project_id=obj.project_id,
            organization_id=obj.organization_id,
            name=obj.name,
            description=obj.description,
            created_by=obj.created_by,
            created_at=obj.created_at.isoformat() if obj.created_at else None,
        )


class IngestJsonRequest(BaseModel):
    id_field: str
    records: list[dict]


class CreateIndexingBatchRequest(BaseModel):
    project_id: int
    document_type_id: int
    record_ids: list[int] = Field(min_length=1)
    agent_id: int


class AssignQaAgentRequest(BaseModel):
    agent_id: int
