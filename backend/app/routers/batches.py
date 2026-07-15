from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_project_access, get_current_user, require_roles
from app.models.batch import Batch
from app.models.document_type import DocumentType
from app.models.project import Project
from app.models.record import Record
from app.schemas.batch import (
    BatchOut, DocumentTypeCreate, DocumentTypeOut, RecordOut,
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
    project = await db.get(Project, project_id)
    check_project_access(project, current_user)
    result = await db.execute(
        select(DocumentType).where(DocumentType.project_id == project_id)
    )
    return list(result.scalars().all())


@router.get("/projects/{project_id}/batches", response_model=list[BatchOut])
async def list_batches(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = await db.get(Project, project_id)
    check_project_access(project, current_user)
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
    project = await db.get(Project, batch.project_id)
    check_project_access(project, current_user)
    return batch


@router.get("/batches/{batch_id}/records", response_model=list[RecordOut])
async def list_records(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    batch = await db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    project = await db.get(Project, batch.project_id)
    check_project_access(project, current_user)
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
    project = await db.get(Project, dt.project_id)
    check_project_access(project, current_user)
    return dt


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


async def _resolve_record_bucket(
    record_id: int, db: AsyncSession, current_user
) -> tuple[Record, str]:
    record = await db.get(Record, record_id)
    if not record or not record.file_reference:
        raise HTTPException(status_code=404, detail="Record or file not found")

    # Resolve project via cabinet (new path) or batch (fallback)
    project = None
    if record.cabinet_id:
        from app.models.cabinet import Cabinet
        cabinet = await db.get(Cabinet, record.cabinet_id)
        if cabinet:
            project = await db.get(Project, cabinet.project_id)
    if project is None and record.batch_id:
        batch = await db.get(Batch, record.batch_id)
        if batch:
            project = await db.get(Project, batch.project_id)

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    check_project_access(project, current_user)
    if not project.s3_bucket_name:
        raise HTTPException(status_code=503, detail="S3 bucket not ready")

    return record, project.s3_bucket_name


async def _resolve_content_type(bucket: str, key: str) -> str:
    content_type = await s3_service.get_object_content_type(bucket, key)
    if content_type == "application/octet-stream":
        content_type = await s3_service.sniff_content_type(bucket, key)
    return content_type


@router.get("/records/{record_id}/view-url")
async def get_view_url(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record, bucket = await _resolve_record_bucket(record_id, db, current_user)
    content_type = await _resolve_content_type(bucket, record.file_reference)
    url = await s3_service.get_presigned_view_url(bucket, record.file_reference, content_type=content_type)
    return {"view_url": url, "content_type": content_type}


@router.get("/records/{record_id}/image")
async def get_record_image(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Streams the record's image/PDF bytes through the backend on the same
    origin as the rest of the API, instead of the client fetching a
    presigned URL from a separate S3/MinIO origin (see get_view_url above,
    kept for callers that still want a direct-to-S3 URL). This is what
    AgentWorkspace/QCWorkspace use for in-line viewing, since a background
    image/tile fetch to an untrusted second origin fails with no
    user-actionable prompt — see s3_service.stream_object."""
    record, bucket = await _resolve_record_bucket(record_id, db, current_user)
    content_type = await _resolve_content_type(bucket, record.file_reference)
    return StreamingResponse(
        s3_service.stream_object(bucket, record.file_reference),
        media_type=content_type,
    )
