"""APScheduler job: marks overdue tasks as stale and releases their locks."""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.metrics import stale_tasks_processed_total
from app.models.audit_log import AuditAction, AuditEntityType
from app.models.record import Record
from app.models.task import Task, TaskStatus
from app.services import audit_service

logger = logging.getLogger(__name__)


async def _run_stale_check() -> None:
    # This job previously ran every 15 minutes completely silently, success
    # or failure — there was no way to tell it was even running short of
    # inferring it from side effects (tasks/locks changing). try/except here
    # is deliberate even though APScheduler already logs unhandled exceptions
    # itself: without it, a mid-loop failure leaves no record of how many
    # tasks (if any) were processed before it broke.
    logger.info("stale check started")
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Task).where(
                    Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
                    Task.due_at <= datetime.now(timezone.utc),
                )
            )
            tasks = list(result.scalars().all())

            for task in tasks:
                old_status = task.status.value
                task.status = TaskStatus.stale
                stale_tasks_processed_total.inc()

                # Need tenant_id — fetch through batch→project. Resolved once,
                # up front, so both audit events below use the real value —
                # this used to be computed after the lock_expired event and
                # only applied to stale_flagged, leaving lock_expired with a
                # hardcoded placeholder tenant_id of 0, which violated the
                # audit_logs.tenant_id FK and aborted the whole run's
                # transaction (no tasks in that run got flagged or unlocked).
                from app.models.batch import Batch
                from app.models.project import Project
                batch = await db.get(Batch, task.batch_id)
                project = await db.get(Project, batch.project_id)

                # Release any lock held by this task's assignee
                record = await db.get(Record, task.record_id)
                if record and record.locked_by == task.assigned_to:
                    record.locked_by = None
                    record.locked_at = None
                    await audit_service.write_event(
                        db,
                        tenant_id=project.tenant_id,
                        entity_type=AuditEntityType.record,
                        entity_id=record.id,
                        action=AuditAction.lock_expired,
                        performed_by=None,
                    )

                await audit_service.write_event(
                    db,
                    tenant_id=project.tenant_id,
                    entity_type=AuditEntityType.task,
                    entity_id=task.id,
                    action=AuditAction.stale_flagged,
                    performed_by=None,
                    old_value={"status": old_status},
                    new_value={"status": "stale"},
                )

            await db.commit()
        logger.info("stale check completed", extra={"tasks_processed": len(tasks)})
    except Exception:
        logger.exception("stale check failed")
        raise


def start_scheduler():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_stale_check,
        trigger="interval",
        minutes=15,
        id="stale_checker",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler
