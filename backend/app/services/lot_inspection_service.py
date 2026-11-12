"""Digitizing supervisor's outgoing ISO 2859-1 inspection of a draft lot,
before release to the customer. See LotInspection's docstring for the data
model; lot_service.release_lot calls assert_releasable as its gate.

Lifecycle of one attempt:
  start_inspection   — random sample drawn, one pending item per record
  review_item        — supervisor marks every field accepted/defective
                       (re-reviewable until the inspection completes)
  complete_inspection — tallies non-conformities against the acceptance
                       number, sends every defective record to rework
  cancel_inspection  — abandons an in-progress attempt (kept in the report)

Counting follows the same rule as the customer's QC
(lot_service.calculate_accuracy): each defective field is one
non-conformity; any critical-field defect fails the lot outright, otherwise
the lot passes when non-critical non-conformities <= acceptance number.
"""
import random
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType
from app.models.batch import Batch
from app.models.document_type import DocumentType
from app.models.lot import Lot, LotRecord, LotStatus
from app.models.lot_inspection import (
    InspectionItemStatus,
    LotInspection,
    LotInspectionFieldResult,
    LotInspectionItem,
    LotInspectionStatus,
)
from app.models.project import Project
from app.models.qc_field_result import QcFieldStatus
from app.models.record import Record, RecordStatus
from app.models.user import User
from app.schemas.task import FieldResultIn
from app.services import aql_service, audit_service, record_service


async def _get_lot(db: AsyncSession, lot_id: int, tenant_id: int) -> Lot:
    lot = await db.get(Lot, lot_id)
    if not lot or lot.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot


async def _get_inspection(db: AsyncSession, *, lot_id: int, inspection_id: int, tenant_id: int) -> LotInspection:
    inspection = await db.get(LotInspection, inspection_id)
    if not inspection or inspection.lot_id != lot_id or inspection.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Inspection not found")
    return inspection


async def _list_inspections(db: AsyncSession, lot_id: int) -> list[LotInspection]:
    result = await db.execute(
        select(LotInspection).where(LotInspection.lot_id == lot_id).order_by(LotInspection.attempt_number)
    )
    return list(result.scalars().all())


_INEFFECTIVE = (LotInspectionStatus.cancelled, LotInspectionStatus.superseded)


def _latest_effective(inspections: list[LotInspection]) -> LotInspection | None:
    """Latest attempt that wasn't cancelled or superseded — the one that
    decides whether the lot may be released or must be re-inspected."""
    effective = [i for i in inspections if i.status not in _INEFFECTIVE]
    return effective[-1] if effective else None


async def supersede_passed(db: AsyncSession, lot: Lot) -> LotInspection | None:
    """Called when a lot changes after it passed (recalled, or records
    removed): the passing attempt no longer clears the lot for release, so a
    fresh inspection is needed. Returns the superseded attempt, if any. The
    caller writes the audit event for the change that caused it."""
    latest = _latest_effective(await _list_inspections(db, lot.id))
    if latest is None or latest.status != LotInspectionStatus.passed:
        return None
    latest.status = LotInspectionStatus.superseded
    return latest


async def _lot_records(db: AsyncSession, lot_id: int) -> list[Record]:
    result = await db.execute(
        select(Record).join(LotRecord, LotRecord.record_id == Record.id).where(LotRecord.lot_id == lot_id)
    )
    return list(result.scalars().all())


async def _schema_properties(db: AsyncSession, document_type_id: int | None) -> dict:
    if document_type_id is None:
        return {}
    doc_type = await db.get(DocumentType, document_type_id)
    return ((doc_type.json_schema or {}).get("properties", {})) if doc_type else {}


async def _audit(db: AsyncSession, *, lot: Lot, user_id: int | None, old_value=None, new_value=None, action):
    await audit_service.write_event(
        db, tenant_id=lot.tenant_id, entity_type=AuditEntityType.lot, entity_id=lot.id,
        action=action, performed_by=user_id, old_value=old_value, new_value=new_value,
    )


async def start_inspection(
    db: AsyncSession,
    *,
    lot_id: int,
    aql_level: float,
    user_id: int,
    tenant_id: int,
) -> LotInspection:
    lot = await _get_lot(db, lot_id, tenant_id)
    if lot.status != LotStatus.draft:
        raise HTTPException(status_code=409, detail="Only draft lots can be inspected before release")

    inspections = await _list_inspections(db, lot_id)
    if any(i.status == LotInspectionStatus.in_progress for i in inspections):
        raise HTTPException(status_code=409, detail="An inspection is already in progress for this lot")
    latest = _latest_effective(inspections)
    if latest is not None and latest.status == LotInspectionStatus.passed:
        raise HTTPException(status_code=409, detail="This lot has already passed inspection")

    records = await _lot_records(db, lot_id)
    if not records:
        raise HTTPException(status_code=422, detail="Lot has no records to inspect")
    # A failed attempt's defective records are out for rework; re-inspecting
    # before they're back would sample a lot that isn't the one being
    # handed over.
    not_ready = [r.id for r in records if r.status != RecordStatus.qa_passed]
    if not_ready:
        raise HTTPException(
            status_code=409,
            detail=f"{len(not_ready)} record(s) in this lot are not qa_passed (awaiting rework): {sorted(not_ready)}",
        )

    plan = aql_service.sampling_plan(len(records), aql_level)
    seed = secrets.token_hex(16)
    sampled_ids = random.Random(seed).sample(sorted(r.id for r in records), plan["sample_size"])
    records_by_id = {r.id: r for r in records}

    batch_ids = {r.batch_id for r in records if r.batch_id is not None}
    doc_type_by_batch: dict[int, int] = {}
    if batch_ids:
        rows = (await db.execute(select(Batch.id, Batch.document_type_id).where(Batch.id.in_(batch_ids)))).all()
        doc_type_by_batch = dict(rows)

    inspection = LotInspection(
        tenant_id=tenant_id,
        lot_id=lot_id,
        attempt_number=(inspections[-1].attempt_number + 1) if inspections else 1,
        status=LotInspectionStatus.in_progress,
        aql_level=aql_level,
        lot_size=len(records),
        random_seed=seed,
        inspected_by=user_id,
        **plan,
    )
    db.add(inspection)
    await db.flush()

    for rid in sorted(sampled_ids):
        record = records_by_id[rid]
        db.add(LotInspectionItem(
            inspection_id=inspection.id,
            record_id=rid,
            document_type_id=doc_type_by_batch.get(record.batch_id),
            record_version=record.current_version,
            status=InspectionItemStatus.pending,
        ))
    await db.flush()

    await _audit(
        db, lot=lot, user_id=user_id, action=AuditAction.sampled,
        new_value={
            "inspection_id": inspection.id,
            "attempt_number": inspection.attempt_number,
            "aql_level": aql_level,
            "lot_size": inspection.lot_size,
            **plan,
            "random_seed": seed,
        },
    )
    return inspection


async def review_item(
    db: AsyncSession,
    *,
    lot_id: int,
    inspection_id: int,
    record_id: int,
    field_results: list[FieldResultIn],
    tenant_id: int,
) -> LotInspectionItem:
    inspection = await _get_inspection(db, lot_id=lot_id, inspection_id=inspection_id, tenant_id=tenant_id)
    if inspection.status != LotInspectionStatus.in_progress:
        raise HTTPException(status_code=409, detail="Inspection is no longer in progress")

    item = (await db.execute(
        select(LotInspectionItem).where(
            LotInspectionItem.inspection_id == inspection_id,
            LotInspectionItem.record_id == record_id,
        )
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Record is not in this inspection's sample")

    properties = await _schema_properties(db, item.document_type_id)
    submitted = {fr.field_key for fr in field_results}
    if len(submitted) != len(field_results) or submitted != set(properties.keys()):
        missing = sorted(set(properties.keys()) - submitted)
        extra = sorted(submitted - set(properties.keys()))
        raise HTTPException(
            status_code=422,
            detail=f"field_results must cover exactly the document type's fields once each — missing {missing}, unexpected {extra}",
        )

    # Re-review replaces the previous marks wholesale.
    existing = (await db.execute(
        select(LotInspectionFieldResult).where(LotInspectionFieldResult.item_id == item.id)
    )).scalars().all()
    for fr in existing:
        await db.delete(fr)

    for fr in field_results:
        db.add(LotInspectionFieldResult(
            item_id=item.id,
            field_key=fr.field_key,
            status=QcFieldStatus(fr.status),
            is_critical=bool(properties.get(fr.field_key, {}).get("x-critical", False)),
            note=fr.note,
        ))
    item.status = (
        InspectionItemStatus.defective
        if any(fr.status == "defective" for fr in field_results)
        else InspectionItemStatus.accepted
    )
    item.reviewed_at = datetime.now(timezone.utc)
    await db.flush()
    return item


async def complete_inspection(
    db: AsyncSession,
    *,
    lot_id: int,
    inspection_id: int,
    summary_note: str | None,
    user_id: int,
    tenant_id: int,
) -> LotInspection:
    lot = await _get_lot(db, lot_id, tenant_id)
    inspection = await _get_inspection(db, lot_id=lot_id, inspection_id=inspection_id, tenant_id=tenant_id)
    if inspection.status != LotInspectionStatus.in_progress:
        raise HTTPException(status_code=409, detail="Inspection is no longer in progress")

    items = (await db.execute(
        select(LotInspectionItem).where(LotInspectionItem.inspection_id == inspection_id)
    )).scalars().all()
    pending = [i.record_id for i in items if i.status == InspectionItemStatus.pending]
    if pending:
        raise HTTPException(
            status_code=409,
            detail=f"{len(pending)} sampled record(s) not yet reviewed: {sorted(pending)}",
        )

    defects = (await db.execute(
        select(LotInspectionItem.record_id, LotInspectionFieldResult)
        .join(LotInspectionFieldResult, LotInspectionFieldResult.item_id == LotInspectionItem.id)
        .where(
            LotInspectionItem.inspection_id == inspection_id,
            LotInspectionFieldResult.status == QcFieldStatus.defective,
        )
        .order_by(LotInspectionFieldResult.id)
    )).all()
    critical = sum(1 for _, fr in defects if fr.is_critical)
    minor = len(defects) - critical
    defective_items = [i for i in items if i.status == InspectionItemStatus.defective]

    inspection.critical_defect_count = critical
    inspection.minor_defect_count = minor
    inspection.defective_record_count = len(defective_items)
    inspection.summary_note = summary_note
    inspection.completed_at = datetime.now(timezone.utc)
    inspection.status = (
        LotInspectionStatus.passed
        if critical == 0 and minor <= inspection.acceptance_number
        else LotInspectionStatus.failed
    )

    # Defective records go back to the indexer whatever the verdict — a
    # passed lot still mustn't be handed over with known errors in it, so
    # release waits for them (assert_releasable) without a re-inspection.
    defects_by_record: dict[int, list[str]] = {}
    for record_id, fr in defects:
        label = fr.field_key + (f" ({fr.note})" if fr.note else "")
        defects_by_record.setdefault(record_id, []).append(label)
    for item in defective_items:
        await record_service.requeue_record(
            db,
            record_id=item.record_id,
            target="indexing",
            supervisor_id=user_id,
            tenant_id=tenant_id,
            note=(
                f"Outgoing inspection #{inspection.attempt_number} of lot {lot.id}: "
                + "; ".join(defects_by_record.get(item.record_id, []))
            ),
        )

    await _audit(
        db, lot=lot, user_id=user_id, action=AuditAction.status_changed,
        old_value={"inspection_id": inspection.id, "status": "in_progress"},
        new_value={
            "inspection_id": inspection.id,
            "status": inspection.status.value,
            "critical_defect_count": critical,
            "minor_defect_count": minor,
            "defective_record_count": len(defective_items),
            "acceptance_number": inspection.acceptance_number,
        },
    )
    await db.flush()
    return inspection


async def cancel_inspection(
    db: AsyncSession,
    *,
    lot_id: int,
    inspection_id: int,
    user_id: int,
    tenant_id: int,
) -> LotInspection:
    lot = await _get_lot(db, lot_id, tenant_id)
    inspection = await _get_inspection(db, lot_id=lot_id, inspection_id=inspection_id, tenant_id=tenant_id)
    if inspection.status != LotInspectionStatus.in_progress:
        raise HTTPException(status_code=409, detail="Only an in-progress inspection can be cancelled")
    inspection.status = LotInspectionStatus.cancelled
    inspection.completed_at = datetime.now(timezone.utc)
    await _audit(
        db, lot=lot, user_id=user_id, action=AuditAction.status_changed,
        old_value={"inspection_id": inspection.id, "status": "in_progress"},
        new_value={"inspection_id": inspection.id, "status": "cancelled"},
    )
    await db.flush()
    return inspection


async def assert_releasable(db: AsyncSession, lot: Lot) -> None:
    """release_lot's gate: the latest non-cancelled inspection passed, and
    every record is qa_passed again (the passing attempt's own defective
    records go to rework too — see complete_inspection)."""
    latest = _latest_effective(await _list_inspections(db, lot.id))
    if latest is None or latest.status != LotInspectionStatus.passed:
        raise HTTPException(
            status_code=409,
            detail="Lot must pass an outgoing ISO 2859-1 inspection before it can be released",
        )
    not_ready = (await db.execute(
        select(func.count()).select_from(Record)
        .join(LotRecord, LotRecord.record_id == Record.id)
        .where(LotRecord.lot_id == lot.id, Record.status != RecordStatus.qa_passed)
    )).scalar_one()
    if not_ready:
        raise HTTPException(
            status_code=409,
            detail=f"{not_ready} record(s) in this lot are still being reworked (not qa_passed)",
        )


async def get_inspection_detail(
    db: AsyncSession,
    *,
    lot_id: int,
    inspection_id: int,
    tenant_id: int,
) -> dict:
    """Working view for the supervisor's review screen — every sampled
    record with its current marks."""
    inspection = await _get_inspection(db, lot_id=lot_id, inspection_id=inspection_id, tenant_id=tenant_id)
    return (await _serialize(db, [inspection], include_pending_items=True))[0]


async def list_inspections(db: AsyncSession, *, lot_id: int, tenant_id: int) -> list[dict]:
    await _get_lot(db, lot_id, tenant_id)
    return await _serialize(db, await _list_inspections(db, lot_id), include_items=False)


async def build_report(db: AsyncSession, *, lot: Lot) -> dict:
    """The outgoing inspection report shared with the customer: the sampling
    plan, verdict and non-conformity tally of every attempt (cancelled ones
    included, so a re-drawn sample is never hidden), plus the per-record
    findings."""
    project = await db.get(Project, lot.project_id)
    lot_size = (await db.execute(
        select(func.count()).select_from(LotRecord).where(LotRecord.lot_id == lot.id)
    )).scalar_one()
    inspections = await _list_inspections(db, lot.id)
    latest = _latest_effective(inspections)
    return {
        "lot_id": lot.id,
        "lot_name": lot.name,
        "lot_status": lot.status.value if hasattr(lot.status, "value") else lot.status,
        "project_name": project.name if project else None,
        "lot_size": lot_size,
        "released_at": lot.released_at,
        "standard": "ISO 2859-1, single sampling, general inspection level II",
        "final_status": latest.status.value if latest else None,
        "inspections": await _serialize(db, inspections, include_pending_items=True),
    }


async def _serialize(
    db: AsyncSession,
    inspections: list[LotInspection],
    *,
    include_items: bool = True,
    include_pending_items: bool = False,
) -> list[dict]:
    if not inspections:
        return []
    inspector_ids = {i.inspected_by for i in inspections}
    inspectors = {
        u.id: u.full_name
        for u in (await db.execute(select(User).where(User.id.in_(inspector_ids)))).scalars().all()
    }

    items_by_inspection: dict[int, list[LotInspectionItem]] = {i.id: [] for i in inspections}
    results_by_item: dict[int, list[LotInspectionFieldResult]] = {}
    records: dict[int, Record] = {}
    titles_by_doc_type: dict[int, dict[str, str]] = {}
    if include_items:
        items = (await db.execute(
            select(LotInspectionItem)
            .where(LotInspectionItem.inspection_id.in_(items_by_inspection.keys()))
            .order_by(LotInspectionItem.id)
        )).scalars().all()
        for item in items:
            items_by_inspection[item.inspection_id].append(item)
        if items:
            item_ids = [i.id for i in items]
            for fr in (await db.execute(
                select(LotInspectionFieldResult)
                .where(LotInspectionFieldResult.item_id.in_(item_ids))
                .order_by(LotInspectionFieldResult.id)
            )).scalars().all():
                results_by_item.setdefault(fr.item_id, []).append(fr)
            record_ids = {i.record_id for i in items}
            records = {
                r.id: r for r in (await db.execute(select(Record).where(Record.id.in_(record_ids)))).scalars().all()
            }
            for dt_id in {i.document_type_id for i in items if i.document_type_id is not None}:
                props = await _schema_properties(db, dt_id)
                titles_by_doc_type[dt_id] = {
                    k: (v.get("title") if isinstance(v, dict) else None) or k for k, v in props.items()
                }

    out = []
    for inspection in inspections:
        entry = {
            "id": inspection.id,
            "lot_id": inspection.lot_id,
            "attempt_number": inspection.attempt_number,
            "status": inspection.status.value,
            "aql_level": inspection.aql_level,
            "lot_size": inspection.lot_size,
            "code_letter": inspection.code_letter,
            "sample_size": inspection.sample_size,
            "acceptance_number": inspection.acceptance_number,
            "rejection_number": inspection.rejection_number,
            "random_seed": inspection.random_seed,
            "critical_defect_count": inspection.critical_defect_count,
            "minor_defect_count": inspection.minor_defect_count,
            "defective_record_count": inspection.defective_record_count,
            "summary_note": inspection.summary_note,
            "inspected_by": inspection.inspected_by,
            "inspector_name": inspectors.get(inspection.inspected_by),
            "started_at": inspection.created_at,
            "completed_at": inspection.completed_at,
        }
        if include_items:
            item_rows = []
            tally: dict[str, dict] = {}
            reviewed = 0
            for item in items_by_inspection[inspection.id]:
                if item.status != InspectionItemStatus.pending:
                    reviewed += 1
                elif not include_pending_items:
                    continue
                titles = titles_by_doc_type.get(item.document_type_id, {})
                record = records.get(item.record_id)
                field_rows = []
                for fr in results_by_item.get(item.id, []):
                    field_rows.append({
                        "field_key": fr.field_key,
                        "title": titles.get(fr.field_key, fr.field_key),
                        "status": fr.status.value,
                        "is_critical": fr.is_critical,
                        "note": fr.note,
                    })
                    if fr.status == QcFieldStatus.defective:
                        t = tally.setdefault(fr.field_key, {
                            "field_key": fr.field_key,
                            "title": titles.get(fr.field_key, fr.field_key),
                            "is_critical": fr.is_critical,
                            "defective_count": 0,
                        })
                        t["defective_count"] += 1
                item_rows.append({
                    "record_id": item.record_id,
                    "source_identifier": record.source_identifier if record else None,
                    "original_filename": record.original_filename if record else None,
                    "document_type_id": item.document_type_id,
                    "record_version": item.record_version,
                    "status": item.status.value,
                    "reviewed_at": item.reviewed_at,
                    "field_results": field_rows,
                })
            entry["reviewed_count"] = reviewed
            entry["items"] = item_rows
            entry["field_tally"] = sorted(tally.values(), key=lambda t: (-t["defective_count"], t["field_key"]))
        out.append(entry)
    return out
