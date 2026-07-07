from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.batch import Batch, BatchStatus
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.models.user import User


async def staff_productivity(
    db: AsyncSession,
    *,
    project_id: int,
    shift_id: int | None = None,
    date_filter: date | None = None,
) -> list[dict]:
    from app.models.shift import UserProjectAssignment

    today = date_filter or date.today()
    day_start = datetime.combine(today, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    # Get staff assigned to the project
    staff_query = select(User).join(
        UserProjectAssignment,
        (UserProjectAssignment.user_id == User.id)
        & (UserProjectAssignment.project_id == project_id)
        & (UserProjectAssignment.is_active == True),  # noqa: E712
    )
    if shift_id:
        staff_query = staff_query.where(UserProjectAssignment.shift_id == shift_id)

    staff_result = await db.execute(staff_query)
    staff = list(staff_result.scalars().all())

    async def _metrics_for(user_id: int, task_type: TaskType) -> dict:
        # Total completed tasks on this project
        total = await db.execute(
            select(func.count(Task.id)).join(Batch, Task.batch_id == Batch.id).where(
                Batch.project_id == project_id,
                Task.assigned_to == user_id,
                Task.task_type == task_type,
                Task.status == TaskStatus.completed,
            )
        )
        total_count = total.scalar() or 0

        # Completed today
        today_q = await db.execute(
            select(func.count(Task.id)).join(Batch, Task.batch_id == Batch.id).where(
                Batch.project_id == project_id,
                Task.assigned_to == user_id,
                Task.task_type == task_type,
                Task.status == TaskStatus.completed,
                Task.completed_at >= day_start,
                Task.completed_at < day_end,
            )
        )
        today_count = today_q.scalar() or 0

        # Average processing time
        avg_q = await db.execute(
            select(func.avg(Task.processing_time_seconds))
            .join(Batch, Task.batch_id == Batch.id)
            .where(
                Batch.project_id == project_id,
                Task.assigned_to == user_id,
                Task.task_type == task_type,
                Task.status == TaskStatus.completed,
                Task.processing_time_seconds.is_not(None),
            )
        )
        avg_time = avg_q.scalar() or 0

        # Error rate (failed / total attempted)
        failed_q = await db.execute(
            select(func.count(Task.id)).join(Batch, Task.batch_id == Batch.id).where(
                Batch.project_id == project_id,
                Task.assigned_to == user_id,
                Task.task_type == task_type,
                Task.status == TaskStatus.failed,
            )
        )
        failed_count = failed_q.scalar() or 0
        total_attempted = total_count + failed_count
        error_rate = round(failed_count / total_attempted, 4) if total_attempted else 0.0

        # Stale tasks
        stale_q = await db.execute(
            select(func.count(Task.id)).join(Batch, Task.batch_id == Batch.id).where(
                Batch.project_id == project_id,
                Task.assigned_to == user_id,
                Task.task_type == task_type,
                Task.status == TaskStatus.stale,
            )
        )
        stale_count = stale_q.scalar() or 0

        # In-progress tasks
        inprogress_q = await db.execute(
            select(func.count(Task.id)).join(Batch, Task.batch_id == Batch.id).where(
                Batch.project_id == project_id,
                Task.assigned_to == user_id,
                Task.task_type == task_type,
                Task.status == TaskStatus.in_progress,
            )
        )
        inprogress_count = inprogress_q.scalar() or 0

        return {
            "total_records_processed": total_count,
            "records_today": today_count,
            "avg_processing_time_seconds": round(float(avg_time)),
            "error_rate": error_rate,
            "stale_task_count": stale_count,
            "tasks_in_progress": inprogress_count,
        }

    rows = []
    for user in staff:
        rows.append({
            "user_id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "indexing": await _metrics_for(user.id, TaskType.indexing),
            "qa": await _metrics_for(user.id, TaskType.qa),
        })
    return rows


async def project_kpis(db: AsyncSession, *, project_id: int) -> dict:
    from app.models.cabinet import Cabinet

    project = await db.get(Project, project_id)

    # Count via cabinet so unassigned (batch_id=NULL) records are included
    total_q = await db.execute(
        select(func.count(Record.id))
        .join(Cabinet, Record.cabinet_id == Cabinet.id)
        .where(Cabinet.project_id == project_id)
    )
    total = total_q.scalar() or 0

    # "Complete" from the digitizing perspective = QA passed or beyond
    _COMPLETE_STATUSES = (
        RecordStatus.qa_passed,
        RecordStatus.qc_pending,
        RecordStatus.qc_passed,
    )
    complete_q = await db.execute(
        select(func.count(Record.id))
        .join(Cabinet, Record.cabinet_id == Cabinet.id)
        .where(
            Cabinet.project_id == project_id,
            Record.status.in_(_COMPLETE_STATUSES),
        )
    )
    complete = complete_q.scalar() or 0

    remaining = total - complete
    completion_pct = round(complete / total * 100, 1) if total else 0.0

    # Throughput: QA-completed records in last 7 days (primary digitizing output metric)
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    throughput_q = await db.execute(
        select(func.count(Task.id)).join(Batch, Task.batch_id == Batch.id).where(
            Batch.project_id == project_id,
            Task.task_type == TaskType.qa,
            Task.status == TaskStatus.completed,
            Task.completed_at >= week_ago,
        )
    )
    week_throughput = throughput_q.scalar() or 0
    daily_rate = week_throughput / 7.0

    projected_end_date = None
    days_to_proposed = None
    on_track = None

    if daily_rate > 0 and remaining > 0:
        days_needed = remaining / daily_rate
        projected_end_date = (datetime.now(timezone.utc) + timedelta(days=days_needed)).date().isoformat()
    elif remaining == 0:
        projected_end_date = date.today().isoformat()

    if project.proposed_end_date:
        days_to_proposed = (project.proposed_end_date - date.today()).days
        if projected_end_date:
            on_track = date.fromisoformat(projected_end_date) <= project.proposed_end_date

    return {
        "project_id": project_id,
        "total_records": total,
        "records_complete": complete,
        "records_remaining": remaining,
        "completion_pct": completion_pct,
        "daily_throughput_rate": round(daily_rate, 1),
        "projected_end_date": projected_end_date,
        "proposed_end_date": project.proposed_end_date.isoformat() if project.proposed_end_date else None,
        "days_to_proposed_end": days_to_proposed,
        "on_track": on_track,
    }


async def burnup_chart_data(db: AsyncSession, *, project_id: int) -> list[dict]:
    """Daily cumulative completed records for the last 30 days + projection."""
    from app.models.cabinet import Cabinet

    project = await db.get(Project, project_id)
    today = date.today()
    start = today - timedelta(days=29)

    total_q = await db.execute(
        select(func.count(Record.id))
        .join(Cabinet, Record.cabinet_id == Cabinet.id)
        .where(Cabinet.project_id == project_id)
    )
    total = total_q.scalar() or 0

    # Fetch QA completion timestamps (primary digitizing output)
    completions_q = await db.execute(
        select(Task.completed_at).join(Batch, Task.batch_id == Batch.id).where(
            Batch.project_id == project_id,
            Task.task_type == TaskType.qa,
            Task.status == TaskStatus.completed,
            Task.completed_at.is_not(None),
        )
    )
    timestamps = [r[0] for r in completions_q.all() if r[0]]

    daily_counts: dict[date, int] = {}
    for ts in timestamps:
        d = ts.date()
        daily_counts[d] = daily_counts.get(d, 0) + 1

    # Build cumulative actual series
    points = []
    cumulative = 0
    for i in range(30):
        d = start + timedelta(days=i)
        cumulative += daily_counts.get(d, 0)
        points.append({"date": d.isoformat(), "completed": cumulative, "projected": None})

    # Compute projected line from last 7-day rate
    recent_total = sum(daily_counts.get(today - timedelta(days=j), 0) for j in range(7))
    daily_rate = recent_total / 7.0
    current_complete = cumulative
    for i in range(1, 31):
        d = today + timedelta(days=i)
        current_complete = min(current_complete + daily_rate, total)
        points.append({"date": d.isoformat(), "completed": None, "projected": round(current_complete, 1)})

    return points
