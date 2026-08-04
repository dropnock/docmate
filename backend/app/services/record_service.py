import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.batch import Batch, BatchStatus, BatchType
from app.models.cabinet import Cabinet
from app.models.record import Record, RecordStatus
from app.models.record_version import VersionReason
from app.models.task import Task, TaskStatus, TaskType
from app.models.user import User
from app.services import audit_service
from app.services.lock_service import release_lock
from app.services.version_service import create_version


async def attach_task_attribution(db: AsyncSession, records: list[Record]) -> None:
    """Sets transient .indexed_by_id/.indexed_by_name/.qa_by_id/.qa_by_name
    attributes on each Record in place — same non-column-attribute pattern
    as batches.py's _attach_indexer_names setting .indexer_name on Batch.

    "Who indexed / who QA'd" = the assignee of the most recently *completed*
    indexing/qa Task for that record — no indexed_by/qa_by column exists on
    Record, and Batch.indexer_name is a per-batch majority-task-count
    heuristic, not per-record attribution.

    Two queries total regardless of len(records) — no N+1.
    """
    record_ids = [r.id for r in records]
    if not record_ids:
        return

    latest = (
        select(
            Task.record_id, Task.task_type,
            func.max(Task.completed_at).label("latest_completed_at"),
        )
        .where(
            Task.record_id.in_(record_ids),
            Task.task_type.in_([TaskType.indexing, TaskType.qa]),
            Task.status == TaskStatus.completed,
        )
        .group_by(Task.record_id, Task.task_type)
        .subquery()
    )
    rows = (await db.execute(
        select(Task.record_id, Task.task_type, Task.assigned_to, User.full_name)
        .join(
            latest,
            (Task.record_id == latest.c.record_id)
            & (Task.task_type == latest.c.task_type)
            & (Task.completed_at == latest.c.latest_completed_at),
        )
        .outerjoin(User, Task.assigned_to == User.id)
    )).all()

    by_record: dict[int, dict[str, tuple[int | None, str | None]]] = {}
    for record_id, task_type, assigned_to, full_name in rows:
        by_record.setdefault(record_id, {})[task_type.value] = (assigned_to, full_name)

    for r in records:
        indexing = by_record.get(r.id, {}).get("indexing", (None, None))
        qa = by_record.get(r.id, {}).get("qa", (None, None))
        r.indexed_by_id, r.indexed_by_name = indexing
        r.qa_by_id, r.qa_by_name = qa


async def list_project_records(
    db: AsyncSession,
    *,
    project_id: int,
    statuses: list[RecordStatus] | None,
    filename: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Record], int]:
    """Project-wide (not batch-scoped) record list. Resolves each record's
    project via Cabinet.project_id first, falling back to Batch.project_id
    (Record.batch_id is nullable) — same precedence
    s3_service.resolve_record_project uses per-record, expressed here as a
    SQL join for bulk-list efficiency.

    filename is a case-insensitive substring match against
    original_filename OR source_identifier (the same fallback the frontend
    table's "Filename" column itself displays) — a plain substring match
    already lets a search like "invoice123" find "invoice123.pdf" with no
    special extension-stripping needed."""
    resolved_project_id = func.coalesce(Cabinet.project_id, Batch.project_id)
    base = (
        select(Record)
        .outerjoin(Cabinet, Record.cabinet_id == Cabinet.id)
        .outerjoin(Batch, Record.batch_id == Batch.id)
        .where(resolved_project_id == project_id)
    )
    if statuses:
        base = base.where(Record.status.in_(statuses))
    if filename:
        pattern = f"%{filename}%"
        base = base.where(
            or_(Record.original_filename.ilike(pattern), Record.source_identifier.ilike(pattern))
        )

    total = (await db.execute(
        select(func.count()).select_from(base.with_only_columns(Record.id).subquery())
    )).scalar_one()

    page = (await db.execute(
        base.order_by(Record.updated_at.desc(), Record.id.desc()).limit(limit).offset(offset)
    )).scalars().all()

    return list(page), total


async def requeue_record(
    db: AsyncSession,
    *,
    record_id: int,
    target: Literal["indexing", "qa"],
    supervisor_id: int,
    tenant_id: int,
    note: str | None = None,
) -> Record:
    """Supervisor-initiated rework trigger — not tied to any in-flight Task
    (unlike task_service.fail_task, which fails a task the caller currently
    holds).

    target="indexing" resets the record to RecordStatus.pending and detaches
    it from any batch (batch_id=None) — the same "raw, not yet indexed"
    state a never-before-batched record is in. This deliberately reuses the
    existing unbatched-pending pool cabinet_service.create_indexing_batch
    already serves (surfaced today via the digitizing portal's Cabinet
    Assignment screen: "Allocate pending records to indexers") rather than
    inventing a bespoke rework batch/task — a record set to qa_failed in a
    dedicated one-off batch had no assignment UI anywhere, since the
    existing screens only know how to allocate *unbatched pending* records
    (create_indexing_batch) or assign QA agents to *qa_review* batches
    (assign_qa_agent). No new Task is created here; one gets created later,
    when a supervisor actually allocates the record to an indexer via that
    existing flow. Requires record.cabinet_id (a record with no cabinet can
    never appear in that pool at all — 400 rather than silently orphaning
    it).

    target="qa" mirrors batch_service.auto_advance_to_qa (RecordStatus.
    qa_pending, new pending qa Task) — but since there's no "unbatched
    qa_pending pool" equivalent, this still creates a dedicated 1-record
    rework Batch (same convention fail_task's QC-fail branch uses) so the
    existing "Assign QA Agent" control (for qa_review batches) can find it.
    Requires record.batch_id, to copy document_type_id/cabinet_id from.
    """
    record = await db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    if target == "indexing" and record.cabinet_id is None:
        raise HTTPException(
            status_code=400,
            detail="Record has no cabinet — cannot re-enter the indexing queue",
        )
    if target == "qa" and record.batch_id is None:
        raise HTTPException(
            status_code=400,
            detail="Record has no associated batch — nothing to copy a rework batch from",
        )
    old_status = record.status

    # Force-unlock unconditionally — same escape hatch as POST
    # /records/{id}/unlock, folded into this transaction. No-op if already
    # unlocked.
    await release_lock(db, record=record, user_id=supervisor_id, tenant_id=tenant_id)

    # Close out any task still open on this record so we never leave two
    # simultaneously-actionable tasks behind, and so calling this twice on
    # the same record is idempotent rather than creating duplicate pending
    # tasks.
    now = datetime.now(timezone.utc)
    open_tasks = (await db.execute(
        select(Task).where(
            Task.record_id == record.id,
            Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
        )
    )).scalars().all()
    for t in open_tasks:
        prior = t.status.value
        t.status = TaskStatus.failed
        t.completed_at = now
        if t.started_at:
            t.processing_time_seconds = int((now - t.started_at).total_seconds())
        await audit_service.write_event(
            db, tenant_id=tenant_id, entity_type=AuditEntityType.task, entity_id=t.id,
            action=AuditAction.status_changed, performed_by=supervisor_id,
            old_value={"status": prior}, new_value={"status": "failed", "reason": "supervisor_requeue"},
        )

    if target == "indexing":
        await create_version(
            db, record=record, reason=VersionReason.supervisor_requeue,
            user_id=supervisor_id, tenant_id=tenant_id,
        )
        record.status = RecordStatus.pending
        record.batch_id = None
        action = AuditAction.requeued_for_indexing
        new_value = {"status": record.status.value}
    else:
        original_batch = await db.get(Batch, record.batch_id)
        rework_batch = Batch(
            project_id=original_batch.project_id,
            cabinet_id=original_batch.cabinet_id,
            document_type_id=original_batch.document_type_id,
            name="",
            batch_type=BatchType.indexing,
            status=BatchStatus.qa_review,
        )
        db.add(rework_batch)
        await db.flush()
        rework_batch.name = f"Supervisor QA Recheck {rework_batch.id} — Record {record.id}"
        record.batch_id = rework_batch.id
        record.status = RecordStatus.qa_pending
        db.add(Task(
            record_id=record.id, batch_id=rework_batch.id, task_type=TaskType.qa,
            assigned_to=None, assigned_by=supervisor_id, status=TaskStatus.pending,
        ))
        action = AuditAction.requeued_for_qa
        new_value = {"status": record.status.value, "batch_id": rework_batch.id}

    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=record.id,
        action=action, performed_by=supervisor_id,
        old_value={"status": old_status.value},
        new_value=new_value,
        metadata={"note": note} if note else None,
    )
    await db.flush()
    return record


async def bulk_requeue_records(
    db: AsyncSession,
    *,
    record_ids: list[int],
    target: Literal["indexing", "qa"],
    supervisor_id: int,
    tenant_id: int,
    note: str | None = None,
) -> list[Record]:
    """Same shape as task_service.bulk_reassign — loops the single-record op
    per id (see requeue_record docstring for per-target behavior)."""
    return [
        await requeue_record(
            db, record_id=rid, target=target, supervisor_id=supervisor_id,
            tenant_id=tenant_id, note=note,
        )
        for rid in record_ids
    ]


async def export_records_zip(db: AsyncSession, *, record_ids: list[int]) -> bytes:
    """One JSON file per record, containing that record's current
    indexed_data. Read-only — no version/audit write, since nothing about
    the record changes."""
    records = (await db.execute(select(Record).where(Record.id.in_(record_ids)))).scalars().all()
    found = {r.id for r in records}
    missing = set(record_ids) - found
    if missing:
        raise HTTPException(status_code=404, detail=f"Record(s) not found: {sorted(missing)}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for r in records:
            payload = {
                "record_id": r.id,
                "source_identifier": r.source_identifier,
                "original_filename": r.original_filename,
                "status": r.status.value,
                "current_version": r.current_version,
                "indexed_data": r.indexed_data or {},
            }
            zf.writestr(f"record_{r.id}.json", json.dumps(payload, indent=2))
    return buf.getvalue()
