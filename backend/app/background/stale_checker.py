"""APScheduler job: marks overdue tasks as stale and releases their locks."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditAction, AuditEntityType
from app.models.record import Record
from app.models.task import Task, TaskStatus
from app.services import audit_service


async def _run_stale_check() -> None:
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

            # Release any lock held by this task's assignee
            record = await db.get(Record, task.record_id)
            if record and record.locked_by == task.assigned_to:
                record.locked_by = None
                record.locked_at = None
                await audit_service.write_event(
                    db,
                    tenant_id=_tenant_id_for_task(task),
                    entity_type=AuditEntityType.record,
                    entity_id=record.id,
                    action=AuditAction.lock_expired,
                    performed_by=None,
                )

            # Need tenant_id — fetch through batch→project
            from app.models.batch import Batch
            from app.models.project import Project
            batch = await db.get(Batch, task.batch_id)
            project = await db.get(Project, batch.project_id)

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


def _tenant_id_for_task(task: Task) -> int:
    # Placeholder — actual lookup happens inside _run_stale_check
    return 0


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
