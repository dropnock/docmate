import asyncio
import logging
import math
import random
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.batch import Batch, BatchStatus, BatchType
from app.models.cabinet import Cabinet
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.services import audit_service, image_service, s3_service, staff_assignment_service
from app.services.task_service import _get_stale_hours

logger = logging.getLogger(__name__)


async def create_cabinet(
    db: AsyncSession,
    *,
    project_id: int,
    organization_id: int | None,
    name: str,
    description: str | None,
    user_id: int,
    tenant_id: int,
) -> Cabinet:
    cabinet = Cabinet(
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
        name=name,
        description=description,
        created_by=user_id,
    )
    db.add(cabinet)
    await db.flush()
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.record,
        entity_id=cabinet.id,
        action=AuditAction.created,
        performed_by=user_id,
        new_value={"name": name, "project_id": project_id},
    )
    return cabinet


async def list_cabinets(
    db: AsyncSession,
    *,
    project_id: int,
    tenant_id: int,
) -> list[Cabinet]:
    result = await db.execute(
        select(Cabinet).where(
            Cabinet.project_id == project_id,
            Cabinet.tenant_id == tenant_id,
        ).order_by(Cabinet.created_at.desc())
    )
    return list(result.scalars().all())


async def get_cabinet(
    db: AsyncSession,
    *,
    cabinet_id: int,
    tenant_id: int,
) -> Cabinet:
    cabinet = await db.get(Cabinet, cabinet_id)
    if not cabinet or cabinet.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return cabinet


async def get_cabinet_records(
    db: AsyncSession,
    *,
    cabinet_id: int,
    status_filter: str | None,
    tenant_id: int,
) -> list[Record]:
    cabinet = await get_cabinet(db, cabinet_id=cabinet_id, tenant_id=tenant_id)
    q = select(Record).where(Record.cabinet_id == cabinet.id)
    if status_filter:
        q = q.where(Record.status == status_filter)
    result = await db.execute(q.order_by(Record.id))
    return list(result.scalars().all())


async def ingest_json_records(
    db: AsyncSession,
    *,
    cabinet_id: int,
    records_payload: list[dict],
    id_field: str,
    user_id: int,
    tenant_id: int,
) -> list[Record]:
    """Create records from a JSON payload. Each item becomes one record."""
    cabinet = await get_cabinet(db, cabinet_id=cabinet_id, tenant_id=tenant_id)
    created: list[Record] = []
    for item in records_payload:
        source_id = str(item.get(id_field, "")) or None
        record = Record(
            cabinet_id=cabinet.id,
            indexed_data=item,
            source_identifier=source_id,
            status=RecordStatus.pending,
        )
        db.add(record)
        created.append(record)
    await db.flush()
    for record in created:
        await audit_service.write_event(
            db,
            tenant_id=tenant_id,
            entity_type=AuditEntityType.record,
            entity_id=record.id,
            action=AuditAction.created,
            performed_by=user_id,
            new_value={"source": "json_ingest", "source_identifier": record.source_identifier},
        )
    return created


async def _convert_tiff_if_needed(db: AsyncSession, record: Record) -> None:
    """Converts a freshly-uploaded TIFF scan to a single multi-page PDF and
    repoints file_reference at it, so viewing never has to decode the TIFF
    again — see batches.py's get_record_image, which performs the same
    conversion as a one-time self-heal for any record that reaches it
    without this having already run (pre-existing data, or a failure here).
    Failures are swallowed: a conversion problem must never block the
    upload confirmation itself."""
    try:
        project = await s3_service.resolve_record_project(record, db)
        if not project or not project.s3_bucket_name:
            return
        bucket = project.s3_bucket_name
        content_type = await s3_service.resolve_content_type(bucket, record.file_reference)
        if content_type != "image/tiff":
            return
        data = await s3_service.get_object_bytes(bucket, record.file_reference)
        pdf_bytes = await asyncio.to_thread(image_service.tiff_to_pdf, data)
        key = s3_service.derived_pdf_key(record.file_reference)
        await s3_service.put_object_bytes(bucket, key, pdf_bytes, "application/pdf")
        record.file_reference = key
    except Exception:
        logger.exception("TIFF-to-PDF conversion failed for record %s", record.id)


async def link_image_to_record(
    db: AsyncSession,
    *,
    cabinet_id: int,
    record_id: int,
    original_filename: str,
    s3_key: str,
    tenant_id: int,
) -> Record:
    """Store the image reference on a record and stamp the original filename."""
    record = await db.get(Record, record_id)
    if not record or record.cabinet_id != cabinet_id:
        raise HTTPException(status_code=404, detail="Record not found in this cabinet")
    record.file_reference = s3_key
    record.original_filename = original_filename
    # Derive source_identifier from filename if not already set
    if not record.source_identifier:
        record.source_identifier = Path(original_filename).stem
    await _convert_tiff_if_needed(db, record)
    return record


async def ingest_image_create_or_link(
    db: AsyncSession,
    *,
    cabinet_id: int,
    original_filename: str,
    s3_key: str,
    tenant_id: int,
    user_id: int,
) -> Record:
    """After image upload: find existing record by source_identifier or create stub."""
    cabinet = await get_cabinet(db, cabinet_id=cabinet_id, tenant_id=tenant_id)
    source_id = Path(original_filename).stem

    existing = (await db.execute(
        select(Record).where(
            Record.cabinet_id == cabinet.id,
            Record.source_identifier == source_id,
        )
    )).scalar_one_or_none()

    if existing:
        existing.file_reference = s3_key
        existing.original_filename = original_filename
        await _convert_tiff_if_needed(db, existing)
        return existing

    # No matching JSON record — create stub
    record = Record(
        cabinet_id=cabinet.id,
        file_reference=s3_key,
        original_filename=original_filename,
        source_identifier=source_id,
        status=RecordStatus.pending,
    )
    db.add(record)
    await db.flush()
    await _convert_tiff_if_needed(db, record)
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.record,
        entity_id=record.id,
        action=AuditAction.created,
        performed_by=user_id,
        new_value={"source": "image_ingest", "original_filename": original_filename},
    )
    return record


async def create_indexing_batch(
    db: AsyncSession,
    *,
    cabinet_id: int,
    project_id: int,
    document_type_id: int,
    record_ids: list[int],
    agent_id: int,
    supervisor_id: int,
    tenant_id: int,
) -> Batch:
    """Create an indexing batch from selected cabinet records and assign tasks."""
    from datetime import timedelta
    from app.models.project import Project

    cabinet = await get_cabinet(db, cabinet_id=cabinet_id, tenant_id=tenant_id)
    project = await db.get(Project, project_id)
    stale_hours = project.stale_threshold_hours if project else 24
    due_at = datetime.now(timezone.utc) + timedelta(hours=stale_hours)

    await staff_assignment_service.require_shift_role_for_task_type(
        db, user_id=agent_id, project_id=project_id, task_type=TaskType.indexing,
    )

    batch = Batch(
        project_id=project_id,
        cabinet_id=cabinet.id,
        document_type_id=document_type_id,
        name="",
        batch_type=BatchType.indexing,
        status=BatchStatus.indexing,
    )
    db.add(batch)
    await db.flush()
    # Name includes the DB-assigned id so it's guaranteed unique even when
    # several batches are created within the same wall-clock second (e.g.
    # allocating to multiple agents in one supervisor action).
    batch.name = f"Batch {batch.id} — {datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    for record_id in record_ids:
        record = await db.get(Record, record_id)
        if not record or record.cabinet_id != cabinet.id:
            continue
        # Re-parent record to this batch
        record.batch_id = batch.id
        task = Task(
            record_id=record_id,
            batch_id=batch.id,
            task_type=TaskType.indexing,
            assigned_to=agent_id,
            assigned_by=supervisor_id,
            status=TaskStatus.pending,
            due_at=due_at,
        )
        db.add(task)

    await db.flush()
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.task,
        entity_id=batch.id,
        action=AuditAction.assigned,
        performed_by=supervisor_id,
        new_value={"agent_id": agent_id, "record_count": len(record_ids), "batch_type": "indexing"},
    )
    return batch
