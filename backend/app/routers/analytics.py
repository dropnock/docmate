import csv
import io
from datetime import date as date_type, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_project_access, require_roles
from app.models.project import Project
from app.models.task import TaskType
from app.models.user import User
from app.services.analytics_service import (
    STAFF_TASK_SORTS, burnup_chart_data, project_kpis, qc_project_summary, qc_staff_productivity,
    records_dashboard, staff_productivity, staff_task_times,
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


# Who may open which report's detail: the same roles as the report itself.
_TASK_DETAIL_ROLES = {
    TaskType.indexing: {"de_supervisor", "customer_supervisor", "admin"},  # staff-productivity
    TaskType.qa: {"de_supervisor", "customer_supervisor", "admin"},
    TaskType.qc: {"customer_supervisor", "admin"},  # qc-staff-productivity
}


@router.get("/staff-productivity/tasks")
async def get_staff_task_times(
    project_id: int,
    user_id: int,
    task_type: Literal["indexing", "qa", "qc"],
    date_from: date_type | None = None,
    date_to: date_type | None = None,
    sort: str = "time_desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    format: Literal["json", "csv"] = "json",
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("de_supervisor", "customer_supervisor", "admin")),
):
    """One agent's time on each record: the detail behind a productivity
    report's Avg Time. format=csv returns every matching row."""
    ttype = TaskType(task_type)
    role = getattr(current_user.role, "value", current_user.role)
    if role not in _TASK_DETAIL_ROLES[ttype]:
        raise HTTPException(status_code=403, detail="Not allowed to view this report")
    if sort not in STAFF_TASK_SORTS:
        raise HTTPException(status_code=422, detail=f"sort must be one of {list(STAFF_TASK_SORTS)}")
    await _authorize(db, project_id, current_user)

    if format == "csv":
        data = await staff_task_times(
            db, project_id=project_id, user_id=user_id, task_type=ttype,
            date_from=date_from, date_to=date_to, sort=sort, offset=0, limit=None,
        )
        agent = await db.get(User, user_id)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["agent", "record", "record_id", "batch", "task_type", "outcome",
                    "started_at", "finished_at", "seconds", "sessions"])
        for r in data["rows"]:
            w.writerow([
                agent.full_name if agent else user_id, r["record_label"], r["record_id"], r["batch_name"],
                task_type, r["outcome"],
                r["started_at"].isoformat() if r["started_at"] else "",
                r["finished_at"].isoformat() if r["finished_at"] else "",
                r["seconds"], r["sessions"] or "",
            ])
        return Response(
            content=buf.getvalue(), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{task_type}-times-user{user_id}.csv"'},
        )

    return await staff_task_times(
        db, project_id=project_id, user_id=user_id, task_type=ttype,
        date_from=date_from, date_to=date_to, sort=sort,
        offset=(page - 1) * page_size, limit=page_size,
    )
