from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.batch import Batch, BatchStatus
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.models.user import User


async def _task_type_metrics(
    db: AsyncSession,
    *,
    project_id: int,
    user_id: int,
    task_type: TaskType,
    day_start: datetime,
    day_end: datetime,
) -> dict:
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
        "tasks_in_progress": inprogress_count,
    }


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

    rows = []
    for user in staff:
        rows.append({
            "user_id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "indexing": await _task_type_metrics(
                db, project_id=project_id, user_id=user.id, task_type=TaskType.indexing,
                day_start=day_start, day_end=day_end,
            ),
            "qa": await _task_type_metrics(
                db, project_id=project_id, user_id=user.id, task_type=TaskType.qa,
                day_start=day_start, day_end=day_end,
            ),
        })
    return rows


async def qc_staff_productivity(
    db: AsyncSession,
    *,
    project_id: int,
    date_filter: date | None = None,
) -> list[dict]:
    """Per-agent QC productivity for the customer portal. Customer QC agents
    aren't assigned via shifts (ShiftRole only covers indexer/qa) — work
    assignment happens per-lot through lot_service.create_qc_batches(), which
    creates Task(task_type=qc, assigned_to=agent_id) rows directly. So the
    staff pool here is the same tenant+customer_org_id+role query used by
    GET /projects/{project_id}/qc-agents, not a shift join."""
    from app.models.user import UserRole

    project = await db.get(Project, project_id)
    today = date_filter or date.today()
    day_start = datetime.combine(today, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    # QC agents, plus customer supervisors who have taken QC batches on this
    # project themselves (a supervisor with none would only be a row of zeros).
    supervisor_with_qc = select(Task.id).join(Batch, Batch.id == Task.batch_id).where(
        Task.assigned_to == User.id, Task.task_type == TaskType.qc, Batch.project_id == project_id,
    ).exists()
    staff_result = await db.execute(
        select(User).where(
            User.tenant_id == project.tenant_id,
            User.organization_id == project.customer_org_id,
            User.is_active == True,  # noqa: E712
            (User.role == UserRole.customer_qc_agent)
            | ((User.role == UserRole.customer_supervisor) & supervisor_with_qc),
        ).order_by(User.full_name)
    )
    staff = list(staff_result.scalars().all())

    rows = []
    for user in staff:
        rows.append({
            "user_id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "qc": await _task_type_metrics(
                db, project_id=project_id, user_id=user.id, task_type=TaskType.qc,
                day_start=day_start, day_end=day_end,
            ),
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

    # Error rate: the share of records the customer's QC reviewed and
    # rejected, across every lot and QC round of the project (ISO 2859-1's
    # "percent nonconforming" — a lot can still pass with rejections inside
    # its acceptance number). Read from QC task outcomes so it works in both
    # sampling modes; manual mode records no per-field marks.
    qc_counts = (await db.execute(
        select(Task.status, func.count(Task.id))
        .join(Batch, Task.batch_id == Batch.id)
        .where(
            Batch.project_id == project_id,
            Task.task_type == TaskType.qc,
            Task.status.in_([TaskStatus.completed, TaskStatus.failed]),
        )
        .group_by(Task.status)
    )).all()
    by_status = {s: n for s, n in qc_counts}
    defects_found = by_status.get(TaskStatus.failed, 0)
    total_inspected = defects_found + by_status.get(TaskStatus.completed, 0)
    error_rate = round(defects_found / total_inspected, 4) if total_inspected else 0.0

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
        "error_rate": error_rate,
        "records_inspected": total_inspected,
        "defects_found": defects_found,
    }


async def qc_project_summary(db: AsyncSession, *, project_id: int) -> dict:
    """Customer-supervisor QC reporting summary — lots quality-checked/
    rejected, records passed, and the QC team's daily throughput. Deliberately
    separate from project_kpis's record-level error_rate."""
    from app.models.lot import Lot, LotRecord, LotStatus

    def _lot_count(*conds):
        return select(func.count(Lot.id)).where(Lot.project_id == project_id, *conds)

    from app.models.lot import LotQcRound

    # Counted per QC round, not per lot: a lot that failed, was resubmitted
    # and then passed has been quality-checked twice and rejected once.
    # Archived rounds (LotQcRound) are always failed ones (see resubmit_lot).
    archived_rounds = (await db.execute(
        select(func.count(LotQcRound.id)).join(Lot, Lot.id == LotQcRound.lot_id)
        .where(Lot.project_id == project_id)
    )).scalar() or 0

    lots_quality_checked = ((await db.execute(
        _lot_count(Lot.qc_completed_at.is_not(None))
    )).scalar() or 0) + archived_rounds

    lots_rejected = ((await db.execute(
        _lot_count(Lot.status.in_([LotStatus.failed, LotStatus.remediation]))
    )).scalar() or 0) + archived_rounds

    # Lifetime snapshot — sampled records currently qc_passed across every
    # lot in the project, regardless of that lot's own status — same
    # all-time-snapshot convention project_kpis's own counts already use.
    records_passed = (await db.execute(
        select(func.count(Record.id))
        .join(LotRecord, LotRecord.record_id == Record.id)
        .join(Lot, Lot.id == LotRecord.lot_id)
        .where(
            Lot.project_id == project_id,
            LotRecord.is_sampled == True,  # noqa: E712
            Record.status == RecordStatus.qc_passed,
        )
    )).scalar() or 0

    # Same 7-day-rate shape as project_kpis's daily_throughput_rate, scoped
    # to QC tasks instead of QA.
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    qc_week_throughput = (await db.execute(
        select(func.count(Task.id)).join(Batch, Task.batch_id == Batch.id).where(
            Batch.project_id == project_id,
            Task.task_type == TaskType.qc,
            Task.status == TaskStatus.completed,
            Task.completed_at >= week_ago,
        )
    )).scalar() or 0

    return {
        "project_id": project_id,
        "lots_quality_checked": lots_quality_checked,
        "lots_rejected": lots_rejected,
        "records_passed": records_passed,
        "qc_daily_throughput": round(qc_week_throughput / 7.0, 1),
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


_BATCHES_INDEXED_STATUSES = (
    BatchStatus.qa_review, BatchStatus.customer_qc, BatchStatus.passed,
    BatchStatus.rejected, BatchStatus.complete,
)
_BATCHES_QA_COMPLETED_STATUSES = (
    BatchStatus.customer_qc, BatchStatus.passed, BatchStatus.rejected, BatchStatus.complete,
)
_BATCHES_TO_BE_QA_STATUSES = (BatchStatus.qa_review,)

# Same "complete" line project_kpis() draws (qa_passed or beyond) — reused
# here so the two dashboards can never silently disagree on what counts as
# QA'd.
_RECORDS_QAD_STATUSES = (RecordStatus.qa_passed, RecordStatus.qc_pending, RecordStatus.qc_passed)


async def _lot_counts(db: AsyncSession, project_id: int) -> dict:
    """Live lot counts for the Records Management dashboard: draft lots by
    how far they are from handover (lot_inspection_service.lot_stage), the
    lots with the customer by status, and the QA-passed records not yet in a
    lot (what the Create Lot picker offers)."""
    from app.models.cabinet import Cabinet
    from app.models.lot import Lot, LotRecord, LotStatus
    from app.services import lot_inspection_service

    by_status = dict((await db.execute(
        select(Lot.status, func.count(Lot.id)).where(Lot.project_id == project_id).group_by(Lot.status)
    )).all())
    stages = {"preparing": 0, "inspecting": 0, "ready": 0}
    drafts = (await db.execute(
        select(Lot).where(Lot.project_id == project_id, Lot.status == LotStatus.draft)
    )).scalars().all()
    for lot in drafts:
        stages[await lot_inspection_service.lot_stage(db, lot)] += 1
    not_in_lot = (await db.execute(
        select(func.count(Record.id))
        .join(Cabinet, Record.cabinet_id == Cabinet.id)
        .where(
            Cabinet.project_id == project_id,
            Record.status == RecordStatus.qa_passed,
            ~select(LotRecord.id).where(LotRecord.record_id == Record.id).exists(),
        )
    )).scalar() or 0
    return {
        "preparing": stages["preparing"],
        "inspecting": stages["inspecting"],
        "ready_to_release": stages["ready"],
        "released": by_status.get(LotStatus.released, 0),
        "in_customer_qc": by_status.get(LotStatus.qc_in_progress, 0),
        "passed": by_status.get(LotStatus.passed, 0),
        "rejected": by_status.get(LotStatus.failed, 0) + by_status.get(LotStatus.remediation, 0),
        "records_not_in_lot": not_in_lot,
    }


async def records_dashboard(
    db: AsyncSession,
    *,
    project_id: int,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    from app.models.cabinet import Cabinet

    def _batch_count(statuses: tuple[BatchStatus, ...]):
        # Filtered on created_at, not completed_at — a batch sitting in
        # qa_review/customer_qc has no completed_at yet, which would
        # silently exclude every still-in-progress batch from any date
        # range (see the History tab's identical fix). date_from/date_to
        # are already full datetimes (the frontend sends start-of-day /
        # end-of-day timestamps) — matching list_batches' own param type,
        # not the plain `date` staff_productivity uses.
        q = select(func.count(Batch.id)).where(
            Batch.project_id == project_id,
            Batch.status.in_(statuses),
        )
        if date_from:
            q = q.where(Batch.created_at >= date_from)
        if date_to:
            q = q.where(Batch.created_at <= date_to)
        return q

    batches_indexed_q = await db.execute(_batch_count(_BATCHES_INDEXED_STATUSES))
    batches_indexed = batches_indexed_q.scalar() or 0

    batches_qa_completed_q = await db.execute(_batch_count(_BATCHES_QA_COMPLETED_STATUSES))
    batches_qa_completed = batches_qa_completed_q.scalar() or 0

    batches_to_be_qad_q = await db.execute(_batch_count(_BATCHES_TO_BE_QA_STATUSES))
    batches_to_be_qad = batches_to_be_qad_q.scalar() or 0

    # Record-level counts are real-time snapshots, not date-filtered — we
    # don't track a per-record "entered this status at" timestamp (only a
    # per-batch one, via completed_at/audit_logs), so there's no meaningful
    # way to scope these to a date range.
    total_q = await db.execute(
        select(func.count(Record.id))
        .join(Cabinet, Record.cabinet_id == Cabinet.id)
        .where(Cabinet.project_id == project_id)
    )
    total_records = total_q.scalar() or 0

    indexed_q = await db.execute(
        select(func.count(Record.id))
        .join(Cabinet, Record.cabinet_id == Cabinet.id)
        .where(
            Cabinet.project_id == project_id,
            Record.status.notin_([RecordStatus.pending, RecordStatus.indexing]),
        )
    )
    total_records_indexed = indexed_q.scalar() or 0

    qad_q = await db.execute(
        select(func.count(Record.id))
        .join(Cabinet, Record.cabinet_id == Cabinet.id)
        .where(Cabinet.project_id == project_id, Record.status.in_(_RECORDS_QAD_STATUSES))
    )
    total_records_qad = qad_q.scalar() or 0

    withdrawn_q = await db.execute(
        select(func.count(Record.id))
        .join(Cabinet, Record.cabinet_id == Cabinet.id)
        .where(Cabinet.project_id == project_id, Record.status == RecordStatus.withdrawn)
    )
    records_withdrawn = withdrawn_q.scalar() or 0

    illegible_q = await db.execute(
        select(func.count(Record.id))
        .join(Cabinet, Record.cabinet_id == Cabinet.id)
        .where(Cabinet.project_id == project_id, Record.status == RecordStatus.illegible)
    )
    records_illegible = illegible_q.scalar() or 0

    # Live, like the record counts: batches in indexing now, and those of them
    # held there by an open flag on one of their records — complete_indexing_batch
    # won't send such a batch on to QA until the flag is resolved or withdrawn.
    from app.models.record_flag import FlagStatus, RecordFlag

    open_flag = (
        select(RecordFlag.id)
        .join(Record, Record.id == RecordFlag.record_id)
        .where(Record.batch_id == Batch.id, RecordFlag.status == FlagStatus.open.value)
        .exists()
    )
    in_indexing = (Batch.project_id == project_id, Batch.status == BatchStatus.indexing)
    batches_being_indexed = (await db.execute(
        select(func.count(Batch.id)).where(*in_indexing)
    )).scalar() or 0
    batches_blocked_by_flags = (await db.execute(
        select(func.count(Batch.id)).where(*in_indexing, open_flag)
    )).scalar() or 0
    records_flagged = (await db.execute(
        select(func.count(func.distinct(RecordFlag.record_id))).where(
            RecordFlag.project_id == project_id, RecordFlag.status == FlagStatus.open.value,
        )
    )).scalar() or 0

    lots = await _lot_counts(db, project_id)

    return {
        "lots": lots,
        "batches_indexed": batches_indexed,
        "batches_qa_completed": batches_qa_completed,
        "batches_to_be_qad": batches_to_be_qad,
        "batches_being_indexed": batches_being_indexed,
        "batches_blocked_by_flags": batches_blocked_by_flags,
        "records_flagged": records_flagged,
        "total_records_indexed": total_records_indexed,
        "total_records_qad": total_records_qad,
        "total_records_remaining": total_records - total_records_qad,
        "records_withdrawn": records_withdrawn,
        "records_illegible": records_illegible,
    }


STAFF_TASK_SORTS = ("time_desc", "time_asc", "finished_desc", "finished_asc")
_SKIP_STATUSES = {"withdrawn", "ineligible", "excluded", "lapsed", "illegible"}


def _task_outcome(task_type: TaskType, status: TaskStatus, record_status: RecordStatus) -> str:
    if status == TaskStatus.in_progress:
        return "in_progress"
    if status == TaskStatus.pending:
        return "not_started"
    if task_type == TaskType.indexing:
        if status == TaskStatus.failed:
            return "requeued"
        return f"skipped_{record_status.value}" if record_status.value in _SKIP_STATUSES else "submitted"
    return "passed" if status == TaskStatus.completed else "rejected"


async def staff_task_times(
    db: AsyncSession,
    *,
    project_id: int,
    user_id: int,
    task_type: TaskType,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "time_desc",
    offset: int = 0,
    limit: int | None = 50,
) -> dict:
    """One agent's time on each record they worked: the per-record detail
    behind staff_productivity's average. Finished tasks (and ones still in
    progress) on the project, dated by when they finished (or were last
    worked on); time is processing_time_seconds once finished, the running
    total so far while open (services/task_timing.py)."""
    seconds = case(
        (Task.status == TaskStatus.in_progress, Task.active_seconds),
        else_=func.coalesce(Task.processing_time_seconds, Task.active_seconds),
    )
    worked_at = func.coalesce(Task.completed_at, Task.last_active_at, Task.started_at)
    conds = [
        Batch.project_id == project_id,
        Task.assigned_to == user_id,
        Task.task_type == task_type,
        Task.status.in_([TaskStatus.completed, TaskStatus.failed, TaskStatus.in_progress]),
    ]
    if date_from:
        conds.append(worked_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        conds.append(worked_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))

    times = [
        t for (t,) in (await db.execute(
            select(seconds).select_from(Task).join(Batch, Task.batch_id == Batch.id)
            .where(*conds, Task.status != TaskStatus.in_progress)
        )).all()
        if t is not None
    ]
    count_all = (await db.execute(
        select(func.count(Task.id)).join(Batch, Task.batch_id == Batch.id).where(*conds)
    )).scalar() or 0
    reopened = (await db.execute(
        select(func.count(Task.id)).join(Batch, Task.batch_id == Batch.id).where(*conds, Task.sessions > 1)
    )).scalar() or 0

    timed = sorted(t for t in times if t)
    summary = {
        "tasks": count_all,
        "finished": len(times),
        "total_seconds": sum(timed),
        "avg_seconds": round(sum(timed) / len(timed)) if timed else 0,
        "median_seconds": (
            0 if not timed else
            timed[len(timed) // 2] if len(timed) % 2 else round((timed[len(timed) // 2 - 1] + timed[len(timed) // 2]) / 2)
        ),
        "fastest_seconds": timed[0] if timed else 0,
        "slowest_seconds": timed[-1] if timed else 0,
        "reopened": reopened,
    }

    order = {
        "time_desc": (seconds.desc(), Task.id.desc()),
        "time_asc": (seconds.asc(), Task.id.asc()),
        "finished_desc": (worked_at.desc(), Task.id.desc()),
        "finished_asc": (worked_at.asc(), Task.id.asc()),
    }[sort]
    q = (
        select(Task, Record, Batch.name)
        .join(Batch, Task.batch_id == Batch.id)
        .join(Record, Task.record_id == Record.id)
        .where(*conds)
        .order_by(*order)
        .offset(offset)
    )
    if limit is not None:
        q = q.limit(limit)
    rows = []
    for task, record, batch_name in (await db.execute(q)).all():
        rows.append({
            "task_id": task.id,
            "record_id": record.id,
            "record_label": record.source_identifier or record.original_filename or f"Record {record.id}",
            "batch_id": task.batch_id,
            "batch_name": batch_name,
            "status": task.status.value,
            "outcome": _task_outcome(task.task_type, task.status, record.status),
            "started_at": task.started_at,
            "finished_at": task.completed_at if task.status != TaskStatus.in_progress else None,
            "seconds": (
                task.active_seconds if task.status == TaskStatus.in_progress or task.processing_time_seconds is None
                else task.processing_time_seconds
            ),
            "sessions": task.sessions,
        })
    return {"summary": summary, "total": count_all, "rows": rows}
