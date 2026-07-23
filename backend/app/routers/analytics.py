from datetime import date as date_type

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_roles
from app.services.analytics_service import (
    burnup_chart_data, project_kpis, records_dashboard, staff_productivity,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/staff-productivity")
async def get_staff_productivity(
    project_id: int,
    shift_id: int | None = None,
    date: date_type | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    return await staff_productivity(db, project_id=project_id, shift_id=shift_id, date_filter=date)


@router.get("/project-kpis/{project_id}")
async def get_project_kpis(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    return await project_kpis(db, project_id=project_id)


@router.get("/project-kpis/{project_id}/burnup")
async def get_burnup(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    return await burnup_chart_data(db, project_id=project_id)


@router.get("/records-dashboard")
async def get_records_dashboard(
    project_id: int,
    date_from: date_type | None = None,
    date_to: date_type | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    return await records_dashboard(db, project_id=project_id, date_from=date_from, date_to=date_to)
