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
from app.models.document_type import DocumentType
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.services import audit_service, image_service, s3_service, staff_assignment_service

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
        entity_type=AuditEntityType.cabinet,
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
    not_in_lot: bool = False,
) -> list[Record]:
    cabinet = await get_cabinet(db, cabinet_id=cabinet_id, tenant_id=tenant_id)
    q = select(Record).where(Record.cabinet_id == cabinet.id)
    if not_in_lot:
        # The Create Lot picker: a record already in a lot can't join another.
        from app.models.lot import LotRecord
        q = q.where(~select(LotRecord.id).where(LotRecord.record_id == Record.id).exists())
    if status_filter:
        q = q.where(Record.status == status_filter)
        # A record keeps status "pending" until its indexer actually starts
        # the task (see task_service.start_task) — so a "pending" filter
        # alone still matches records already parented to another batch
        # awaiting pickup. Exclude those too, or the batch-assignment picker
        # offers records that are already spoken for.
        if status_filter == RecordStatus.pending.value:
            q = q.where(Record.batch_id.is_(None))
    result = await db.execute(q.order_by(Record.id))
    return list(result.scalars().all())


MAX_LISTED_PROBLEMS = 10


def _identifier(item: dict, id_field: str) -> str | None:
    """The item's identifier at id_field, which may be a dotted path into
    nested objects ("md.title_name"). None when it's missing or blank, or
    isn't a plain string/number."""
    value = item
    for part in id_field.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return str(value).strip() or None


def _problem_summary(problems: list[str]) -> str:
    listed = "; ".join(problems[:MAX_LISTED_PROBLEMS])
    more = len(problems) - MAX_LISTED_PROBLEMS
    return listed + (f"; and {more} more" if more > 0 else "")


async def ingest_json_records(
    db: AsyncSession,
    *,
    cabinet_id: int,
    records_payload: list[dict],
    id_field: str,
    user_id: int,
    tenant_id: int,
) -> tuple[list[Record], list[Record]]:
    """Create records from a JSON payload. Each item becomes one record —
    or fills in the record an image was already uploaded for, when that
    record has the same identifier and no data yet.

    The identifier is what links an item to its image (the image's filename
    without its extension), so every item must have one, unique within the
    import and not already taken by a record that has data. Otherwise
    nothing is imported (422): an item without one would become a record no
    image can ever link to. Returns (created, linked)."""
    id_field = id_field.strip()
    if not id_field:
        raise HTTPException(status_code=422, detail="Identifier field name is required")
    if not records_payload:
        raise HTTPException(status_code=422, detail="The JSON holds no records")
    cabinet = await get_cabinet(db, cabinet_id=cabinet_id, tenant_id=tenant_id)

    identifiers = [_identifier(item, id_field) for item in records_payload]
    missing = [i + 1 for i, ident in enumerate(identifiers) if ident is None]
    if missing:
        shown = ", ".join(str(n) for n in missing[:MAX_LISTED_PROBLEMS])
        more = len(missing) - MAX_LISTED_PROBLEMS
        raise HTTPException(
            status_code=422,
            detail=(
                f'{len(missing)} of {len(records_payload)} record(s) have no "{id_field}" value '
                f"(record {shown}{f' and {more} more' if more > 0 else ''}). "
                "Each record needs the identifier its image is named after; "
                'use a dotted path such as "md.title_name" for a nested field.'
            ),
        )

    seen: dict[str, int] = {}
    problems: list[str] = []
    for n, ident in enumerate(identifiers, start=1):
        if ident in seen:
            problems.append(f'"{ident}" is in records {seen[ident]} and {n}')
        else:
            seen[ident] = n

    existing = {
        r.source_identifier: r
        for r in (await db.execute(
            select(Record).where(
                Record.cabinet_id == cabinet.id,
                Record.source_identifier.in_(list(seen)),
            )
        )).scalars()
    }
    problems.extend(
        f'"{ident}" already has data (record {r.id})'
        for ident, r in existing.items() if r.indexed_data
    )
    if problems:
        raise HTTPException(
            status_code=422,
            detail=f"Nothing imported — {_problem_summary(problems)}.",
        )

    created: list[Record] = []
    linked: list[Record] = []
    for item, ident in zip(records_payload, identifiers):
        record = existing.get(ident)
        if record is not None:
            record.indexed_data = item
            linked.append(record)
            continue
        record = Record(
            cabinet_id=cabinet.id,
            indexed_data=item,
            source_identifier=ident,
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
    for record in linked:
        await audit_service.write_event(
            db,
            tenant_id=tenant_id,
            entity_type=AuditEntityType.record,
            entity_id=record.id,
            action=AuditAction.data_imported,
            performed_by=user_id,
            new_value={"source": "json_ingest", "source_identifier": record.source_identifier},
        )
    return created, linked


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
    if not record_ids:
        raise HTTPException(status_code=400, detail="record_ids must not be empty")

    cabinet = await get_cabinet(db, cabinet_id=cabinet_id, tenant_id=tenant_id)
    # A batch must stay inside its cabinet's project: its records, document
    # type and the project it's reported and exported under are all the same one.
    if cabinet.project_id != project_id:
        raise HTTPException(status_code=400, detail="This cabinet belongs to another project")
    document_type = await db.get(DocumentType, document_type_id)
    if not document_type or document_type.project_id != project_id:
        raise HTTPException(status_code=400, detail="This document type belongs to another project")

    await staff_assignment_service.require_shift_role_for_task_type(
        db, user_id=agent_id, project_id=project_id, task_type=TaskType.indexing,
    )

    # Only records that are pending and not already parented to a batch are
    # eligible — otherwise a record already in-flight in another batch would
    # get silently re-parented and double-assigned.
    result = await db.execute(
        select(Record).where(
            Record.id.in_(record_ids),
            Record.cabinet_id == cabinet.id,
            Record.status == RecordStatus.pending,
            Record.batch_id.is_(None),
        )
    )
    eligible_records = {r.id: r for r in result.scalars().all()}
    ineligible_ids = [rid for rid in record_ids if rid not in eligible_records]
    if ineligible_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "Records must be pending and not already in a batch: "
                f"ineligible record ids {ineligible_ids}"
            ),
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
        record = eligible_records[record_id]
        record.batch_id = batch.id
        task = Task(
            record_id=record_id,
            batch_id=batch.id,
            task_type=TaskType.indexing,
            assigned_to=agent_id,
            assigned_by=supervisor_id,
            status=TaskStatus.pending,
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
