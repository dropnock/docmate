from pydantic import BaseModel


class DocumentTypeCreate(BaseModel):
    name: str
    json_schema: dict


class DocumentTypeOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    project_id: int
    name: str
    json_schema: dict


class CreateRecordsRequest(BaseModel):
    count: int = 1


class BatchCreate(BaseModel):
    project_id: int
    document_type_id: int
    name: str


class BatchOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    project_id: int
    document_type_id: int
    name: str
    status: str
    aql_level_snapshot: float | None
    aql_sample_size: int | None


class RecordOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    batch_id: int
    file_reference: str | None
    indexed_data: dict | None
    current_version: int
    locked_by: int | None
    status: str


class UploadUrlResponse(BaseModel):
    upload_url: str
    s3_key: str


class ConfirmUploadRequest(BaseModel):
    s3_key: str


class IndexDataRequest(BaseModel):
    indexed_data: dict


class RecordVersionOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    record_id: int
    version_number: int
    indexed_data: dict
    created_by: int
    reason: str


class AuditEventOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    entity_type: str
    entity_id: int
    action: str
    performed_by: int | None
    performed_at: str
    old_value: dict | None
    new_value: dict | None
    metadata_: dict | None
