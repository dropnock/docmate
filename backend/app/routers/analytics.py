from datetime import date as date_type, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_project_access, require_roles
from app.models.project import Project
from app.services.analytics_service import (
    burnup_chart_data, project_kpis, qc_project_summary, qc_staff_productivity, records_dashboard,
    staff_productivity,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


async def _authorize(db: AsyncSession, project_id: int, current_user) -> None:
    """404 unless the project is in the caller's tenant and, for a customer,
    belongs to their organisation (a tenant holds several customers)."""
    check_project_access(await db.get(Project, project_id), current_user)


@router.get("/staff-productivity")
async def get_staff_productivity(
    project_id: int,
    shift_id: int | None = None,
    date: date_type | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    await _authorize(db, project_id, current_user)
    return await staff_productivity(db, project_id=project_id, shift_id=shift_id, date_filter=date)


@router.get("/qc-staff-productivity")
async def get_qc_staff_productivity(
    project_id: int,
    date: date_type | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    await _authorize(db, project_id, current_user)
    return await qc_staff_productivity(db, project_id=project_id, date_filter=date)


@router.get("/project-kpis/{project_id}")
async def get_project_kpis(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    await _authorize(db, project_id, current_user)
    return await project_kpis(db, project_id=project_id)


@router.get("/project-kpis/{project_id}/burnup")
async def get_burnup(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    await _authorize(db, project_id, current_user)
    return await burnup_chart_data(db, project_id=project_id)


@router.get("/project-kpis/{project_id}/qc-summary")
async def get_qc_summary(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    await _authorize(db, project_id, current_user)
    return await qc_project_summary(db, project_id=project_id)


@router.get("/records-dashboard")
async def get_records_dashboard(
    project_id: int,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    await _authorize(db, project_id, current_user)
    return await records_dashboard(db, project_id=project_id, date_from=date_from, date_to=date_to)
