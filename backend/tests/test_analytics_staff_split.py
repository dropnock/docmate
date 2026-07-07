"""staff_productivity must report indexing and QA performance independently."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TaskType
from app.services import analytics_service, task_service


class TestStaffProductivitySplit:
    async def test_independent_indexing_and_qa_metrics(self, db: AsyncSession, seed):
        # indexer completes one indexing task
        idx_task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(db, task_id=idx_task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id)
        await task_service.complete_task(
            db, task_id=idx_task.id, user_id=seed["indexer"].id,
            tenant_id=seed["tenant"].id, indexed_data={"title": "A"},
        )

        # qa_staff completes one QA task
        qa_task = await task_service.assign_task(
            db, record_id=seed["record2"].id, batch_id=seed["batch"].id,
            task_type=TaskType.qa, agent_id=seed["qa_staff"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(db, task_id=qa_task.id, user_id=seed["qa_staff"].id, tenant_id=seed["tenant"].id)
        await task_service.complete_task(
            db, task_id=qa_task.id, user_id=seed["qa_staff"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        rows = await analytics_service.staff_productivity(db, project_id=seed["project"].id)
        by_user = {r["user_id"]: r for r in rows}

        assert "role" not in by_user[seed["indexer"].id]
        assert by_user[seed["indexer"].id]["indexing"]["total_records_processed"] == 1
        assert by_user[seed["indexer"].id]["qa"]["total_records_processed"] == 0

        assert by_user[seed["qa_staff"].id]["qa"]["total_records_processed"] == 1
        assert by_user[seed["qa_staff"].id]["indexing"]["total_records_processed"] == 0
