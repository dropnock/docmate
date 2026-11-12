from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_project_access, require_roles
from app.models.lot import LotStatus
from app.schemas.lot import (
    ApplySampleRequest,
    CompleteInspectionRequest,
    CreateQcBatchesRequest,
    LotCreate,
    LotOut,
    QcFieldResultsOut,
    ReviewInspectionItemRequest,
    StartInspectionRequest,
)
from app.services import aql_service, lot_inspection_service, lot_service

router = APIRouter(prefix="/api/lots", tags=["lots"])


@router.post("", status_code=201)
async def create_lot(
    body: LotCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
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
    lots = await lot_service.list_lots(
        db, project_id=project_id, tenant_id=current_user._tenant_id
    )
    return [LotOut.model_validate(lot) for lot in lots]


@router.get("/{lot_id}")
async def get_lot(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    from sqlalchemy import select
    from app.models.lot import Lot, LotRecord
    from app.models.record import Record

    lot = await lot_service._get_lot(db, lot_id, current_user._tenant_id)
    lot_records_result = await db.execute(
        select(LotRecord).where(LotRecord.lot_id == lot_id)
    )
    lot_records = list(lot_records_result.scalars().all())

    records_out = []
    for lr in lot_records:
        record = await db.get(Record, lr.record_id)
        if record:
            records_out.append({
                "record_id": record.id,
                "source_identifier": record.source_identifier,
                "original_filename": record.original_filename,
                "status": record.status,
                "is_sampled": lr.is_sampled,
            })

    return {**LotOut.model_validate(lot).model_dump(), "records": records_out}


@router.post("/{lot_id}/release")
async def release_lot(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    lot = await lot_service.release_lot(
        db, lot_id=lot_id, user_id=current_user.id, tenant_id=current_user._tenant_id
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
    lot = await lot_service.send_for_remediation(
        db, lot_id=lot_id, user_id=current_user.id, tenant_id=current_user._tenant_id
    )
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
    return {
        "available_aql_levels": list(aql_service.AVAILABLE_AQLS),
        # Pre-selects the customer's current AQL; the supervisor may pick
        # any tabulated level for the internal inspection.
        "default_aql_level": await aql_service.get_current_aql_level(db, lot.project_id),
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
        db, lot_id=lot_id, aql_level=body.aql_level,
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
    from app.models.project import Project
    from app.models.user import Portal

    lot = await lot_service._get_lot(db, lot_id, current_user._tenant_id)
    check_project_access(await db.get(Project, lot.project_id), current_user)
    if current_user.portal == Portal.customer and lot.status == LotStatus.draft:
        raise HTTPException(status_code=404, detail="Lot not found")
    return await lot_inspection_service.build_report(db, lot=lot)


@router.get("/{lot_id}/history")
async def get_lot_history(
    lot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    """The lot's own audit trail. Customers only get it for their own
    organisation's released lots, and only from the first release onward."""
    from app.models.project import Project
    from app.models.user import Portal
    from app.services import audit_service

    lot = await lot_service._get_lot(db, lot_id, current_user._tenant_id)
    check_project_access(await db.get(Project, lot.project_id), current_user)
    is_customer = current_user.portal == Portal.customer
    if is_customer and lot.status == LotStatus.draft:
        raise HTTPException(status_code=404, detail="Lot not found")
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
