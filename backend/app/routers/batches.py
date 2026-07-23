import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_project_access, get_current_user, require_roles
from app.models.batch import Batch, BatchStatus
from app.models.document_type import DocumentType
from app.models.project import Project
from app.models.record import Record
from app.models.task import Task, TaskType
from app.models.user import User
from app.schemas.batch import (
    BatchOut, BatchReassignRequest, DocumentTypeCreate, DocumentTypeOut, RecordOut,
)
from app.schemas.task import TaskOut
from app.services import batch_service, image_service, s3_service

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


async def _attach_indexer_names(db: AsyncSession, batches: list[Batch]) -> list[Batch]:
    """Batches have no first-class assignee — the indexer is whoever holds
    the most indexing tasks in the batch (in practice a single agent, since
    create_indexing_batch assigns every task to one agent_id at creation
    time, but tasks can drift to other agents via reassignment)."""
    batch_ids = [b.id for b in batches]
    if not batch_ids:
        return batches

    counts = await db.execute(
        select(Task.batch_id, Task.assigned_to, func.count())
        .where(
            Task.batch_id.in_(batch_ids),
            Task.task_type == TaskType.indexing,
            Task.assigned_to.is_not(None),
        )
        .group_by(Task.batch_id, Task.assigned_to)
    )
    indexer_by_batch: dict[int, int] = {}
    best_count: dict[int, int] = {}
    for batch_id, assigned_to, count in counts.all():
        if count > best_count.get(batch_id, 0):
            best_count[batch_id] = count
            indexer_by_batch[batch_id] = assigned_to

    user_ids = set(indexer_by_batch.values())
    names: dict[int, str] = {}
    if user_ids:
        users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
        names = {u.id: u.full_name for u in users_result.scalars().all()}

    for b in batches:
        b.indexer_name = names.get(indexer_by_batch.get(b.id))
    return batches


async def _attach_record_counts(db: AsyncSession, batches: list[Batch]) -> list[Batch]:
    batch_ids = [b.id for b in batches]
    if not batch_ids:
        return batches

    counts = await db.execute(
        select(Record.batch_id, func.count())
        .where(Record.batch_id.in_(batch_ids))
        .group_by(Record.batch_id)
    )
    count_by_batch = dict(counts.all())
    for b in batches:
        b.record_count = count_by_batch.get(b.id, 0)
    return batches


@router.get("/projects/{project_id}/batches", response_model=list[BatchOut])
async def list_batches(
    project_id: int,
    status: BatchStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = await db.get(Project, project_id)
    check_project_access(project, current_user)
    query = select(Batch).where(Batch.project_id == project_id)
    if status is not None:
        query = query.where(Batch.status == status)
    if date_from is not None:
        query = query.where(Batch.completed_at >= date_from)
    if date_to is not None:
        query = query.where(Batch.completed_at <= date_to)
    result = await db.execute(query)
    batches = list(result.scalars().all())
    batches = await _attach_indexer_names(db, batches)
    return await _attach_record_counts(db, batches)


@router.post("/batches/{batch_id}/reassign", response_model=list[TaskOut])
async def reassign_batch(
    batch_id: int,
    body: BatchReassignRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    batch = await db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    project = await db.get(Project, batch.project_id)
    check_project_access(project, current_user)
    return await batch_service.reassign_batch(
        db,
        batch_id=batch_id,
        task_type=TaskType(body.task_type),
        new_agent_id=body.agent_id,
        supervisor_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )


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


@router.post("/batches/{batch_id}/complete-indexing", response_model=BatchOut)
async def complete_indexing_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """The indexer's explicit "Complete Batch" action from My Tasks — see
    batch_service.complete_indexing_batch for the completeness check and why
    this is no longer automatic."""
    batch = await db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    project = await db.get(Project, batch.project_id)
    check_project_access(project, current_user)
    return await batch_service.complete_indexing_batch(
        db, batch_id=batch_id, user_id=current_user.id, tenant_id=current_user._tenant_id,
    )


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

    project = await s3_service.resolve_record_project(record, db)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    check_project_access(project, current_user)
    if not project.s3_bucket_name:
        raise HTTPException(status_code=503, detail="S3 bucket not ready")

    return record, project.s3_bucket_name


@router.get("/records/{record_id}/view-url")
async def get_view_url(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    record, bucket = await _resolve_record_bucket(record_id, db, current_user)
    content_type = await s3_service.resolve_content_type(bucket, record.file_reference)
    url = await s3_service.get_presigned_view_url(bucket, record.file_reference, content_type=content_type)
    return {"view_url": url, "content_type": content_type}


@router.get("/records/{record_id}/image")
async def get_record_image(
    record_id: int,
    page: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Streams the record's image/PDF bytes through the backend on the same
    origin as the rest of the API, instead of the client fetching a
    presigned URL from a separate S3/MinIO origin (see get_view_url above,
    kept for callers that still want a direct-to-S3 URL). This is what
    AgentWorkspace/QCWorkspace use for in-line viewing, since a background
    image/tile fetch to an untrusted second origin fails with no
    user-actionable prompt — see s3_service.stream_object.

    TIFF originals are converted to a single multi-page PDF at upload time
    (see cabinet_service._convert_tiff_if_needed) so the browser's own PDF
    viewer handles pagination/zoom natively — `record.file_reference` is
    repointed at the derived PDF once that happens, and this endpoint just
    streams whatever it currently points to. The self-heal branch below only
    fires for a record that reaches this endpoint still pointing at a TIFF
    (pre-existing data the backfill script hasn't reached yet, or a failed
    upload-time conversion) — it converts once and persists the result so
    every subsequent view is a plain passthrough. `page` is a no-op for PDFs
    (kept for backward-compatible callers; the PDF viewer paginates itself)."""
    record, bucket = await _resolve_record_bucket(record_id, db, current_user)
    content_type = await s3_service.resolve_content_type(bucket, record.file_reference)
    if content_type == "image/tiff":
        data = await s3_service.get_object_bytes(bucket, record.file_reference)
        pdf_bytes = await asyncio.to_thread(image_service.tiff_to_pdf, data)
        key = s3_service.derived_pdf_key(record.file_reference)
        await s3_service.put_object_bytes(bucket, key, pdf_bytes, "application/pdf")
        record.file_reference = key
        await db.commit()
        content_type = "application/pdf"
    return StreamingResponse(
        s3_service.stream_object(bucket, record.file_reference),
        media_type=content_type,
    )
