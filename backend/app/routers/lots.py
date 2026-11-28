from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_project_access, require_roles
from app.models.lot import Lot, LotStatus
from app.models.project import Project
from app.models.user import Portal
from app.schemas.lot import (
    ApplySampleRequest,
    CompleteInspectionRequest,
    CreateQcBatchesRequest,
    LotCreate,
    LotOut,
    LotQcRoundOut,
    OverrideMarkRequest,
    QcFieldResultsOut,
    RecallLotRequest,
    RecordOutcomeRequest,
    RemoveLotRecordsRequest,
    ReviewInspectionItemRequest,
    StartInspectionRequest,
)
from app.services import aql_service, lot_inspection_service, lot_service

router = APIRouter(prefix="/api/lots", tags=["lots"])


async def _get_authorized_lot(db: AsyncSession, lot_id: int, current_user) -> Lot:
    """Fetch a lot and enforce the same boundary as check_project_access: a
    tenant can hold several customer organisations, so a customer user only
    reaches lots of their own organisation's projects — and never a draft
    (not yet handed over, or recalled). 404 either way, so neither the lot
    nor its draft state leaks."""
    lot = await lot_service._get_lot(db, lot_id, current_user._tenant_id)
    check_project_access(await db.get(Project, lot.project_id), current_user)
    if current_user.portal == Portal.customer and lot.status == LotStatus.draft:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot


@router.post("", status_code=201)
async def create_lot(
    body: LotCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    check_project_access(await db.get(Project, body.project_id), current_user)
    lot = await lot_service.create_lot(
        db,
        project_id=body.project_id,
        name=body.name,
        description=body.description,
        record_ids=body.record_ids,
        user_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return LotOut.model_validate(lot)


@router.get("/project/{project_id}")
async def list_lots(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    check_project_access(await db.get(Project, project_id), current_user)
    lots = await lot_service.list_lots(
        db, project_id=project_id, tenant_id=current_user._tenant_id,
        exclude_drafts=current_user.portal == Portal.customer,
    )
    return [LotOut.model_validate(lot) for lot in lots]


@router.get("/project/{project_id}/qc-batches")
async def list_qc_batches(
    project_id: int,
    lot_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    """Every customer QC batch of the project (or one lot): who holds its
    tasks and how far it has got — the customer supervisor's view of the
    work handed out, and where they reassign it from."""
    check_project_access(await db.get(Project, project_id), current_user)
    if lot_id is not None:
        lot = await _get_authorized_lot(db, lot_id, current_user)
        if lot.project_id != project_id:
            raise HTTPException(status_code=404, detail="Lot not found")
    return await lot_service.list_qc_batches(db, project_id=project_id, lot_id=lot_id)


@router.get("/{lot_id}")
async def get_lot(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    from sqlalchemy import select
    from app.models.lot import Lot, LotRecord
    from app.models.record import Record

    lot = await _get_authorized_lot(db, lot_id, current_user)
    lot_records_result = await db.execute(
        select(LotRecord).where(LotRecord.lot_id == lot_id)
    )
    lot_records = list(lot_records_result.scalars().all())

    from app.models.user import User

    qc_tasks = await lot_service.latest_qc_tasks(db, [lr.record_id for lr in lot_records])
    holder_ids = {t.assigned_to for t in qc_tasks.values() if t.assigned_to}
    holders = {u.id: u for u in (await db.execute(
        select(User).where(User.id.in_(holder_ids))
    )).scalars()} if holder_ids else {}

    records_out = []
    for lr in lot_records:
        record = await db.get(Record, lr.record_id)
        if record:
            task = qc_tasks.get(record.id)
            holder = holders.get(task.assigned_to) if task else None
            records_out.append({
                "record_id": record.id,
                "source_identifier": record.source_identifier,
                "original_filename": record.original_filename,
                "status": record.status,
                "is_sampled": lr.is_sampled,
                # The record's current customer QC task, if it has one.
                "qc_task": {
                    "task_id": task.id, "batch_id": task.batch_id, "status": task.status.value,
                    "assigned_to": task.assigned_to,
                    "assigned_to_name": holder.full_name if holder else None,
                } if task else None,
            })

    rounds = await lot_service.list_qc_rounds(db, lot_id=lot.id)
    return {
        **LotOut.model_validate(lot).model_dump(),
        "records": records_out,
        # Earlier customer QC rounds (failed, then resubmitted); the current
        # round is the lot's own fields.
        "qc_rounds": [LotQcRoundOut.model_validate(r).model_dump() for r in rounds],
    }


@router.post("/{lot_id}/release")
async def release_lot(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    await _get_authorized_lot(db, lot_id, current_user)
    lot = await lot_service.release_lot(
        db, lot_id=lot_id, user_id=current_user.id, tenant_id=current_user._tenant_id
    )
    await db.commit()
    return LotOut.model_validate(lot)


@router.post("/{lot_id}/recall")
async def recall_lot(
    lot_id: int,
    body: RecallLotRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    """Take a released lot back before the customer samples it. It returns
    to draft and needs a fresh outgoing inspection before release."""
    await _get_authorized_lot(db, lot_id, current_user)
    lot = await lot_service.recall_lot(
        db, lot_id=lot_id, reason=body.reason,
        user_id=current_user.id, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return LotOut.model_validate(lot)


@router.post("/{lot_id}/remove-records")
async def remove_lot_records(
    lot_id: int,
    body: RemoveLotRecordsRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    await _get_authorized_lot(db, lot_id, current_user)
    lot = await lot_service.remove_lot_records(
        db, lot_id=lot_id, record_ids=body.record_ids,
        user_id=current_user.id, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return LotOut.model_validate(lot)


@router.post("/{lot_id}/resubmit")
async def resubmit_lot(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    """Put a remediated lot back to draft for re-inspection and re-release,
    once the records the customer rejected are back through QA."""
    await _get_authorized_lot(db, lot_id, current_user)
    lot = await lot_service.resubmit_lot(
        db, lot_id=lot_id, user_id=current_user.id, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return LotOut.model_validate(lot)


@router.post("/{lot_id}/sample")
async def apply_sample(
    lot_id: int,
    body: ApplySampleRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    await _get_authorized_lot(db, lot_id, current_user)
    lot = await lot_service.apply_sample(
        db,
        lot_id=lot_id,
        sample_rate=body.sample_rate,
        user_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return LotOut.model_validate(lot)


@router.post("/{lot_id}/qc-batches", status_code=201)
async def create_qc_batches(
    lot_id: int,
    body: CreateQcBatchesRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    await _get_authorized_lot(db, lot_id, current_user)
    batches = await lot_service.create_qc_batches(
        db,
        lot_id=lot_id,
        project_id=body.project_id,
        document_type_id=body.document_type_id,
        assignments=[a.model_dump() for a in body.assignments],
        supervisor_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return [{"id": b.id, "name": b.name} for b in batches]


@router.get("/{lot_id}/qc-field-results", response_model=QcFieldResultsOut)
async def get_qc_field_results(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    await _get_authorized_lot(db, lot_id, current_user)
    tabulation = await lot_service.get_field_result_tabulation(
        db, lot_id=lot_id, tenant_id=current_user._tenant_id
    )
    return QcFieldResultsOut(**tabulation)


@router.post("/{lot_id}/send-for-remediation")
async def send_for_remediation(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    await _get_authorized_lot(db, lot_id, current_user)
    lot = await lot_service.send_for_remediation(
        db, lot_id=lot_id, user_id=current_user.id, tenant_id=current_user._tenant_id
    )
    await db.commit()
    return LotOut.model_validate(lot)


# ── Customer QC supervisor's review and decision ────────────────────────────
# Once every sampled record has been reviewed the lot is awaiting_decision:
# the supervisor checks the defects, may change reviewers' marks, then
# confirms the verdict. See lot_service.override_qc_mark / decide_lot.


@router.get("/{lot_id}/qc-review")
async def get_qc_review(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    await _get_authorized_lot(db, lot_id, current_user)
    return await lot_service.get_qc_review(db, lot_id=lot_id, tenant_id=current_user._tenant_id)


@router.put("/{lot_id}/qc-review/{record_id}/fields/{field_key:path}")
async def override_qc_mark(
    lot_id: int,
    record_id: int,
    field_key: str,
    body: OverrideMarkRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    await _get_authorized_lot(db, lot_id, current_user)
    result = await lot_service.override_qc_mark(
        db, lot_id=lot_id, record_id=record_id, field_key=field_key,
        status=body.status, error_type=body.error_type, note=body.note,
        user_id=current_user.id, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return result


@router.put("/{lot_id}/qc-review/{record_id}/outcome")
async def override_record_outcome(
    lot_id: int,
    record_id: int,
    body: RecordOutcomeRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    await _get_authorized_lot(db, lot_id, current_user)
    result = await lot_service.override_record_outcome(
        db, lot_id=lot_id, record_id=record_id, passed=body.passed, note=body.note,
        user_id=current_user.id, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return result


@router.post("/{lot_id}/decide")
async def decide_lot(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    await _get_authorized_lot(db, lot_id, current_user)
    lot = await lot_service.decide_lot(db, lot_id=lot_id, user_id=current_user.id, tenant_id=current_user._tenant_id)
    await db.commit()
    return LotOut.model_validate(lot)


# ── Outgoing inspection (digitizing supervisor, before release) ─────────────
# See lot_inspection_service for the lifecycle.


@router.get("/{lot_id}/inspections")
async def list_inspections(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    lot = await lot_service._get_lot(db, lot_id, current_user._tenant_id)
    aql_level = await aql_service.get_current_aql_level(db, lot.project_id)
    lot_size = await lot_service.count_lot_records(db, lot_id=lot.id)
    general_level = await aql_service.get_general_level(db, lot.project_id)
    aqls = aql_service.config_class_aqls(await aql_service.get_config(db, lot.project_id))
    return {
        "aql_level": aql_level,
        # The project's ISO 2859-1 general inspection level (I / II / III).
        "general_level": general_level.value,
        # What ISO 2859-1's switching rules suggest for this lot; the
        # supervisor chooses the level actually used.
        "suggested_level": (await aql_service.get_suggested_level(db, lot.project_id)).value,
        "lot_size": lot_size,
        # The project's critical / major / minor AQLs (critical None = zero
        # tolerance) and the plan each level would give this lot with them.
        "class_aqls": aqls,
        "level_plans": aql_service.class_plans_by_level(lot_size, aqls, general_level) if lot_size else {},
        "inspections": await lot_inspection_service.list_inspections(
            db, lot_id=lot_id, tenant_id=current_user._tenant_id
        ),
    }


@router.post("/{lot_id}/inspections", status_code=201)
async def start_inspection(
    lot_id: int,
    body: StartInspectionRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    inspection = await lot_inspection_service.start_inspection(
        db, lot_id=lot_id, inspection_level=body.inspection_level,
        user_id=current_user.id, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return await lot_inspection_service.get_inspection_detail(
        db, lot_id=lot_id, inspection_id=inspection.id, tenant_id=current_user._tenant_id
    )


@router.get("/{lot_id}/inspections/{inspection_id}")
async def get_inspection(
    lot_id: int,
    inspection_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    return await lot_inspection_service.get_inspection_detail(
        db, lot_id=lot_id, inspection_id=inspection_id, tenant_id=current_user._tenant_id
    )


@router.put("/{lot_id}/inspections/{inspection_id}/items/{record_id}")
async def review_inspection_item(
    lot_id: int,
    inspection_id: int,
    record_id: int,
    body: ReviewInspectionItemRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    item = await lot_inspection_service.review_item(
        db, lot_id=lot_id, inspection_id=inspection_id, record_id=record_id,
        field_results=body.field_results, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return {"record_id": item.record_id, "status": item.status.value}


@router.post("/{lot_id}/inspections/{inspection_id}/complete")
async def complete_inspection(
    lot_id: int,
    inspection_id: int,
    body: CompleteInspectionRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    await lot_inspection_service.complete_inspection(
        db, lot_id=lot_id, inspection_id=inspection_id, summary_note=body.summary_note,
        user_id=current_user.id, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return await lot_inspection_service.get_inspection_detail(
        db, lot_id=lot_id, inspection_id=inspection_id, tenant_id=current_user._tenant_id
    )


@router.post("/{lot_id}/inspections/{inspection_id}/cancel")
async def cancel_inspection(
    lot_id: int,
    inspection_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    await lot_inspection_service.cancel_inspection(
        db, lot_id=lot_id, inspection_id=inspection_id,
        user_id=current_user.id, tenant_id=current_user._tenant_id,
    )
    await db.commit()
    return {"id": inspection_id, "status": "cancelled"}


@router.get("/{lot_id}/inspection-report")
async def get_inspection_report(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    """Shared with the customer alongside the released lot. Customers only
    see it once the lot has been handed over (not while it's still a draft
    being inspected), and only for their own organisation's projects."""
    lot = await _get_authorized_lot(db, lot_id, current_user)
    # Customers see the inspection level, not the AQL number behind it.
    return await lot_inspection_service.build_report(
        db, lot=lot, include_aql=current_user.portal != Portal.customer,
    )


@router.get("/{lot_id}/history")
async def get_lot_history(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    """The lot's own audit trail. Customers only get it for their own
    organisation's released lots, and only from the first release onward."""
    from app.services import audit_service

    await _get_authorized_lot(db, lot_id, current_user)
    is_customer = current_user.portal == Portal.customer
    events = await audit_service.get_lot_history(
        db, tenant_id=current_user._tenant_id, lot_id=lot_id, from_first_release=is_customer
    )
    return [
        {
            "id": e.id,
            "entity_type": e.entity_type.value,
            "entity_id": e.entity_id,
            "action": e.action.value,
            "performed_by": e.performed_by,
            "actor_name": e.actor.full_name if e.actor else None,
            "performed_at": e.performed_at.isoformat(),
            "old_value": e.old_value,
            "new_value": e.new_value,
            "metadata": e.metadata_,
        }
        for e in events
    ]
