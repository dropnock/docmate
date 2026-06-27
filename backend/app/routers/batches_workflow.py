"""Batch workflow transitions and QC result endpoints."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.services import batch_service
from app.schemas.batch import BatchOut

router = APIRouter(prefix="/api/batches", tags=["batch-workflow"])


class QCResultRequest(BaseModel):
    defects_found: int


class RejectRecordRequest(BaseModel):
    reason: str | None = None


@router.post("/{batch_id}/submit", response_model=BatchOut)
async def submit_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    return await batch_service.submit_batch(
        db, batch_id=batch_id, supervisor_id=current_user.id, tenant_id=current_user._tenant_id
    )


@router.post("/{batch_id}/advance-indexing", response_model=BatchOut)
async def advance_to_indexing(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    return await batch_service.advance_to_indexing(
        db, batch_id=batch_id, supervisor_id=current_user.id, tenant_id=current_user._tenant_id
    )


@router.post("/{batch_id}/advance-qa", response_model=BatchOut)
async def advance_to_qa(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    return await batch_service.advance_to_qa(
        db, batch_id=batch_id, supervisor_id=current_user.id, tenant_id=current_user._tenant_id
    )


@router.post("/{batch_id}/advance-customer-qc", response_model=BatchOut)
async def advance_to_customer_qc(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "admin")),
):
    return await batch_service.advance_to_customer_qc(
        db, batch_id=batch_id, supervisor_id=current_user.id, tenant_id=current_user._tenant_id
    )


@router.post("/{batch_id}/qc-result")
async def record_qc_result(
    batch_id: int,
    body: QCResultRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    return await batch_service.record_qc_result(
        db,
        batch_id=batch_id,
        defects_found=body.defects_found,
        performed_by=current_user.id,
        tenant_id=current_user._tenant_id,
    )


@router.post("/records/{record_id}/reject")
async def reject_record(
    record_id: int,
    body: RejectRecordRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_qc_agent", "customer_supervisor", "admin")),
):
    await batch_service.reject_record_by_customer(
        db,
        record_id=record_id,
        user_id=current_user.id,
        tenant_id=current_user._tenant_id,
        reason=body.reason,
    )
    return {"status": "record rejected and versioned"}
