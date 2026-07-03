from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.project import Project
from app.models.record import Record
from app.schemas.cabinet import (
    AssignQaAgentRequest,
    CabinetOut,
    CreateIndexingBatchRequest,
    IngestJsonRequest,
)
from app.services import cabinet_service, batch_service, s3_service

router = APIRouter(prefix="/api/cabinets", tags=["cabinets"])


@router.get("/project/{project_id}")
async def list_cabinets(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    cabinets = await cabinet_service.list_cabinets(
        db, project_id=project_id, tenant_id=current_user._tenant_id
    )
    return [CabinetOut.from_orm_dt(c) for c in cabinets]


@router.get("/{cabinet_id}")
async def get_cabinet(
    cabinet_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    cabinet = await cabinet_service.get_cabinet(
        db, cabinet_id=cabinet_id, tenant_id=current_user._tenant_id
    )
    return CabinetOut.from_orm_dt(cabinet)


@router.get("/{cabinet_id}/records")
async def get_cabinet_records(
    cabinet_id: int,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    records = await cabinet_service.get_cabinet_records(
        db, cabinet_id=cabinet_id, status_filter=status, tenant_id=current_user._tenant_id
    )
    return [
        {
            "id": r.id,
            "source_identifier": r.source_identifier,
            "original_filename": r.original_filename,
            "file_reference": r.file_reference,
            "status": r.status,
            "current_version": r.current_version,
            "has_image": r.file_reference is not None,
            "has_data": r.indexed_data is not None,
            "cabinet_id": r.cabinet_id,
        }
        for r in records
    ]


@router.post("/{cabinet_id}/ingest-json", status_code=201)
async def ingest_json(
    cabinet_id: int,
    body: IngestJsonRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    records = await cabinet_service.ingest_json_records(
        db,
        cabinet_id=cabinet_id,
        records_payload=body.records,
        id_field=body.id_field,
        user_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return {"created": len(records)}


@router.post("/{cabinet_id}/upload-url")
async def get_upload_url(
    cabinet_id: int,
    filename: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    cabinet = await cabinet_service.get_cabinet(
        db, cabinet_id=cabinet_id, tenant_id=current_user._tenant_id
    )
    project = await db.get(Project, cabinet.project_id)
    if not project or not project.s3_bucket_name:
        raise HTTPException(status_code=400, detail="Project has no S3 bucket")
    key = f"cabinets/{cabinet_id}/{filename}"
    url = await s3_service.get_presigned_upload_url(project.s3_bucket_name, key)
    return {"upload_url": url, "key": key}


@router.patch("/{cabinet_id}/confirm-upload")
async def confirm_upload(
    cabinet_id: int,
    original_filename: str,
    s3_key: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    record = await cabinet_service.ingest_image_create_or_link(
        db,
        cabinet_id=cabinet_id,
        original_filename=original_filename,
        s3_key=s3_key,
        tenant_id=current_user._tenant_id,
        user_id=current_user.id,
    )
    await db.commit()
    return {"id": record.id, "source_identifier": record.source_identifier}


@router.get("/{cabinet_id}/records/{record_id}/view-url")
async def get_image_view_url(
    cabinet_id: int,
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    cabinet = await cabinet_service.get_cabinet(
        db, cabinet_id=cabinet_id, tenant_id=current_user._tenant_id
    )
    result = await db.execute(
        select(Record).where(Record.id == record_id, Record.cabinet_id == cabinet_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    if not record.file_reference:
        raise HTTPException(status_code=404, detail="Record has no image")
    project = await db.get(Project, cabinet.project_id)
    if not project or not project.s3_bucket_name:
        raise HTTPException(status_code=400, detail="Project has no S3 bucket")
    url = await s3_service.get_presigned_view_url(project.s3_bucket_name, record.file_reference)
    return {"url": url}


@router.post("/{cabinet_id}/batches", status_code=201)
async def create_indexing_batch(
    cabinet_id: int,
    body: CreateIndexingBatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    batch = await cabinet_service.create_indexing_batch(
        db,
        cabinet_id=cabinet_id,
        project_id=body.project_id,
        document_type_id=body.document_type_id,
        record_ids=body.record_ids,
        agent_id=body.agent_id,
        supervisor_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return {"id": batch.id, "name": batch.name, "status": batch.status}


@router.patch("/batches/{batch_id}/assign-qa")
async def assign_qa_agent(
    batch_id: int,
    body: AssignQaAgentRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    batch = await batch_service.assign_qa_agent(
        db,
        batch_id=batch_id,
        agent_id=body.agent_id,
        supervisor_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return {"id": batch.id, "status": batch.status}
