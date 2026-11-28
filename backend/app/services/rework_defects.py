"""What a record was sent back for, shown to the indexer and QA agent who fix
it (GET /api/records/{id}/rework-defects, the banner in AgentWorkspace).

A record goes back to the digitizing team from:

- the digitizing supervisor's outgoing inspection (complete_inspection
  requeues each defective sampled record to indexing): the fields marked
  defective, with error type, class and note;
- customer QC, once the customer QC supervisor confirms the lot
  (decide_lot → task_service.route_to_rework): the record's effective marks,
  i.e. the supervisor's override where there is one;
- a QA rejection (fail_task): the QA agent's reason, no fields;
- a supervisor's manual requeue with a note (record_service.requeue_record).

Only the latest send-back counts, and only until the rework is through QA:
once a QA task on the record completes after it, nothing is open any more.
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.batch import Batch
from app.models.lot import Lot
from app.models.lot_inspection import (
    InspectionItemStatus, LotInspection, LotInspectionFieldResult, LotInspectionItem, LotInspectionStatus,
)
from app.models.qc_field_result import QcFieldResult, QcFieldStatus
from app.models.record import Record
from app.models.task import Task, TaskStatus, TaskType
from app.models.user import User
from app.services import defect_class_service, review_fields
from app.services.lot_inspection_service import _schema

# complete_inspection's own requeue note starts like this; that send-back is
# already reported field by field from the inspection itself.
INSPECTION_NOTE_PREFIX = "Outgoing inspection #"


def _aware(at: datetime) -> datetime:
    # SQLite (the tests) hands back naive datetimes; they are all UTC.
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _field(schema: dict, key: str, error_type, severity, note: str | None) -> dict:
    et = getattr(error_type, "value", error_type)
    return {
        "field_key": key,
        "label": review_fields.label_for(schema, key),
        "error_type": et,
        "error_label": defect_class_service.ERROR_TYPE_LABELS.get(et) if et else None,
        "severity": getattr(severity, "value", severity),
        "note": note or None,
    }


async def _name(db: AsyncSession, user_id: int | None) -> str | None:
    user = await db.get(User, user_id) if user_id else None
    return user.full_name if user else None


async def _inspection(db: AsyncSession, record_id: int) -> dict | None:
    row = (await db.execute(
        select(LotInspectionItem, LotInspection)
        .join(LotInspection, LotInspection.id == LotInspectionItem.inspection_id)
        .where(
            LotInspectionItem.record_id == record_id,
            LotInspectionItem.status == InspectionItemStatus.defective,
            LotInspection.status.in_([
                LotInspectionStatus.passed, LotInspectionStatus.failed, LotInspectionStatus.superseded,
            ]),
            LotInspection.completed_at.is_not(None),
        )
        .order_by(LotInspection.completed_at.desc(), LotInspection.id.desc())
        .limit(1)
    )).first()
    if row is None:
        return None
    item, inspection = row
    schema = await _schema(db, item.document_type_id)
    marks = (await db.execute(
        select(LotInspectionFieldResult)
        .where(LotInspectionFieldResult.item_id == item.id, LotInspectionFieldResult.status == QcFieldStatus.defective)
        .order_by(LotInspectionFieldResult.id)
    )).scalars().all()
    lot = await db.get(Lot, inspection.lot_id)
    return {
        "at": inspection.completed_at,
        "source": "inspection",
        "title": f"Outgoing inspection #{inspection.attempt_number} of lot {lot.name if lot else inspection.lot_id}",
        "by": await _name(db, inspection.inspected_by),
        "note": None,
        "fields": [_field(schema, m.field_key, m.error_type, m.severity, m.note) for m in marks],
    }


async def _customer_qc(db: AsyncSession, record_id: int) -> dict | None:
    """The record's latest QC task, if its lot's decision sent it back."""
    task = (await db.execute(
        select(Task).where(
            Task.record_id == record_id, Task.task_type == TaskType.qc,
            Task.status.in_([TaskStatus.completed, TaskStatus.failed]), Task.completed_at.is_not(None),
        ).order_by(Task.completed_at.desc(), Task.id.desc()).limit(1)
    )).scalar_one_or_none()
    if task is None:
        return None
    marks = (await db.execute(
        select(QcFieldResult).where(QcFieldResult.task_id == task.id).order_by(QcFieldResult.id)
    )).scalars().all()
    if not marks:
        return None
    lot = await db.get(Lot, marks[0].lot_id)
    if lot is None:
        return None
    if marks[0].qc_round == lot.qc_round:
        # Rework waits for the customer QC supervisor's decision (decide_lot).
        if lot.decided_at is None:
            return None
        at = lot.decided_at
    else:
        # An earlier round, archived by resubmit_lot — its rework went
        # through QA before the lot could be resubmitted.
        at = task.completed_at
    batch = await db.get(Batch, task.batch_id) if task.batch_id else None
    schema = await _schema(db, batch.document_type_id if batch else None)
    fields = []
    for m in marks:
        status, error_type, severity = defect_class_service.effective(m)
        if status == "defective":
            note = m.override_note if m.override_status is not None else m.note
            fields.append(_field(schema, m.field_key, error_type, severity, note))
    if not fields:
        return None
    return {
        "at": at,
        "source": "customer_qc",
        "title": f"Customer QC of lot {lot.name}",
        # The customer's own people aren't named to the digitizing team.
        "by": None,
        "note": None,
        "fields": fields,
    }


async def _audited(db: AsyncSession, record_id: int) -> dict | None:
    """The latest QA rejection, or a supervisor requeue that came with a note."""
    events = (await db.execute(
        select(AuditLog).where(
            AuditLog.entity_type == AuditEntityType.record, AuditLog.entity_id == record_id,
            AuditLog.action.in_([
                AuditAction.qa_failed, AuditAction.requeued_for_indexing, AuditAction.requeued_for_qa,
            ]),
        ).order_by(AuditLog.performed_at.desc(), AuditLog.id.desc())
    )).scalars().all()
    for e in events:
        meta: dict[str, Any] = e.metadata_ or {}
        if e.action == AuditAction.qa_failed:
            text, title = meta.get("reason"), "QA check"
        else:
            text, title = meta.get("note"), "Supervisor requeue"
            if not text or str(text).startswith(INSPECTION_NOTE_PREFIX):
                continue
        return {
            "at": e.performed_at, "source": "qa" if e.action == AuditAction.qa_failed else "supervisor",
            "title": title, "by": await _name(db, e.performed_by), "note": text or None, "fields": [],
        }
    return None


async def _qa_completed_since(db: AsyncSession, record_id: int, since: datetime) -> bool:
    return (await db.execute(
        select(Task.id).where(
            Task.record_id == record_id, Task.task_type == TaskType.qa,
            Task.status == TaskStatus.completed, Task.completed_at > since,
        ).limit(1)
    )).first() is not None


async def open_rework_defects(db: AsyncSession, record: Record) -> dict | None:
    """The send-back the record is being reworked for, or None."""
    candidates = [c for c in (
        await _inspection(db, record.id),
        await _customer_qc(db, record.id),
        await _audited(db, record.id),
    ) if c is not None]
    if not candidates:
        return None
    for c in candidates:
        c["at"] = _aware(c["at"])
    latest = max(candidates, key=lambda c: c["at"])
    if await _qa_completed_since(db, record.id, latest["at"]):
        return None
    return latest
