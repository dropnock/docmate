"""QC-rejected records must become assignable to a QA agent for remediation."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Batch, BatchStatus, RecordStatus, Task, TaskStatus, TaskType
from app.services import batch_service, task_service


class TestQcRejectionCreatesReworkBatch:
    async def test_fail_qc_task_creates_assignable_qa_rework(self, db: AsyncSession, seed):
        qc_task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.qc, agent_id=seed["qc_agent"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(
            db, task_id=qc_task.id, user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id,
        )

        await task_service.fail_task(
            db, task_id=qc_task.id, user_id=seed["qc_agent"].id,
            reason="Illegible signature", tenant_id=seed["tenant"].id,
        )
        await db.flush()

        record = await db.get(type(seed["record"]), seed["record"].id)
        assert record.status == RecordStatus.qc_failed

        rework_task = (await db.execute(
            select(Task).where(
                Task.record_id == record.id,
                Task.task_type == TaskType.qa,
                Task.status == TaskStatus.pending,
                Task.assigned_to.is_(None),
            )
        )).scalar_one()

        rework_batch = await db.get(Batch, rework_task.batch_id)
        assert rework_batch.status == BatchStatus.qa_review
        assert record.batch_id == rework_batch.id

        # Now assignable to a QA agent via the normal batch-level flow
        await batch_service.assign_qa_agent(
            db, batch_id=rework_batch.id, agent_id=seed["qa_staff"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await db.refresh(rework_task)
        assert rework_task.assigned_to == seed["qa_staff"].id
