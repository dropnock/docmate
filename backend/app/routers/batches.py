import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.batch import Batch, BatchStatus
from app.models.document_type import DocumentType
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.schemas.batch import (
    BatchCreate, BatchOut, ConfirmUploadRequest, CreateRecordsRequest,
    DocumentTypeCreate, DocumentTypeOut, RecordOut, UploadUrlResponse,
)
from app.services import s3_service

router = APIRouter(prefix="/api", tags=["batches"])


@router.post("/document-types", response_model=DocumentTypeOut, status_code=201)
async def create_document_type(
    body: DocumentTypeCreate,
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    dt = DocumentType(project_id=project_id, name=body.name, json_schema=body.json_schema)
    db.add(dt)
    await db.flush()
    return dt


@router.get("/projects/{project_id}/document-types", response_model=list[DocumentTypeOut])
async def list_document_types(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = await db.execute(
        select(DocumentType).where(DocumentType.project_id == project_id)
    )
    return list(result.scalars().all())


@router.post("/batches", response_model=BatchOut, status_code=201)
async def create_batch(
    body: BatchCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin", "de_supervisor")),
):
    batch = Batch(
        project_id=body.project_id,
        document_type_id=body.document_type_id,
        name=body.name,
    )
    db.add(batch)
    await db.flush()
    return batch


@router.get("/projects/{project_id}/batches", response_model=list[BatchOut])
async def list_batches(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = await db.execute(
        select(Batch).where(Batch.project_id == project_id)
    )
    return list(result.scalars().all())


@router.get("/batches/{batch_id}", response_model=BatchOut)
async def get_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    batch = await db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@router.get("/batches/{batch_id}/records", response_model=list[RecordOut])
async def list_records(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = await db.execute(select(Record).where(Record.batch_id == batch_id))
    return list(result.scalars().all())


@router.get("/document-types/{doc_type_id}", response_model=DocumentTypeOut)
async def get_document_type(
    doc_type_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    dt = await db.get(DocumentType, doc_type_id)
    if not dt:
        raise HTTPException(status_code=404, detail="Document type not found")
    return dt


@router.post("/batches/{batch_id}/records", response_model=list[RecordOut], status_code=201)
async def create_records(
    batch_id: int,
    body: CreateRecordsRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    batch = await db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    records = [Record(batch_id=batch_id) for _ in range(max(1, min(body.count, 500)))]
    db.add_all(records)
    await db.flush()
    return records


@router.delete("/records/{record_id}", status_code=204)
async def delete_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    record = await db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    if record.status != RecordStatus.pending:
        raise HTTPException(status_code=409, detail="Only pending records with no indexed data can be deleted")
    await db.delete(record)


@router.patch("/document-types/{doc_type_id}", response_model=DocumentTypeOut)
async def update_document_type(
    doc_type_id: int,
    body: DocumentTypeCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    dt = await db.get(DocumentType, doc_type_id)
    if not dt:
        raise HTTPException(status_code=404, detail="Document type not found")
    dt.name = body.name
    dt.json_schema = body.json_schema
    return dt


@router.post("/records/{record_id}/upload-url", response_model=UploadUrlResponse)
async def get_upload_url(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    record = await db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    # Fetch project to get bucket name
    batch = await db.get(Batch, record.batch_id)
    project = await db.get(Project, batch.project_id)
    if not project.s3_bucket_name:
        raise HTTPException(status_code=503, detail="S3 bucket not ready")

    s3_key = f"records/{record_id}/{uuid.uuid4()}"
    url = s3_service.get_presigned_upload_url(project.s3_bucket_name, s3_key)
    return UploadUrlResponse(upload_url=url, s3_key=s3_key)


@router.patch("/records/{record_id}/confirm-upload", response_model=RecordOut)
async def confirm_upload(
    record_id: int,
    body: ConfirmUploadRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    record = await db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    record.file_reference = body.s3_key
    return record


@router.get("/records/{record_id}/view-url")
async def get_view_url(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record = await db.get(Record, record_id)
    if not record or not record.file_reference:
        raise HTTPException(status_code=404, detail="Record or file not found")

    batch = await db.get(Batch, record.batch_id)
    project = await db.get(Project, batch.project_id)
    bucket = project.s3_bucket_name
    content_type = s3_service.get_object_content_type(bucket, record.file_reference)
    if content_type == "application/octet-stream":
        content_type = s3_service.sniff_content_type(bucket, record.file_reference)
    # Embed content type + inline disposition into the presigned URL so all browsers
    # render the file inline instead of downloading it
    url = s3_service.get_presigned_view_url(bucket, record.file_reference, content_type=content_type)
    return {"view_url": url, "content_type": content_type}


