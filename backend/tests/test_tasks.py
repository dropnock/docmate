"""Task assignment, bulk-reassign, and stale detection tests."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskStatus, TaskType
from app.services import task_service
from tests.conftest import token


class TestTaskAssignment:
    async def test_assign_single_task(self, db: AsyncSession, seed):
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        assert task.id is not None
        assert task.assigned_to == seed["indexer"].id
        assert task.status == TaskStatus.pending
        assert task.due_at is not None

    async def test_start_task_acquires_lock(self, db: AsyncSession, seed):
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        started = await task_service.start_task(
            db, task_id=task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        assert started.status == TaskStatus.in_progress
        assert started.started_at is not None

        from app.models import Record
        record = await db.get(Record, seed["record"].id)
        assert record.locked_by == seed["indexer"].id

    async def test_complete_task_releases_lock(self, db: AsyncSession, seed):
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(
            db, task_id=task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        completed = await task_service.complete_task(
            db, task_id=task.id, user_id=seed["indexer"].id,
            tenant_id=seed["tenant"].id, indexed_data={"title": "Done"},
        )
        assert completed.status == TaskStatus.completed
        assert completed.processing_time_seconds is not None

        from app.models import Record
        record = await db.get(Record, seed["record"].id)
        assert record.locked_by is None

    async def test_complete_qa_task_persists_edits_and_creates_version(self, db: AsyncSession, seed):
        from app.models import Record, RecordVersion
        from sqlalchemy import select as sa_select

        record = seed["record"]
        record.indexed_data = {"title": "Original"}
        record.current_version = 2
        await db.flush()

        qa_task = await task_service.assign_task(
            db, record_id=record.id, batch_id=seed["batch"].id,
            task_type=TaskType.qa, agent_id=seed["qa_staff"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(
            db, task_id=qa_task.id, user_id=seed["qa_staff"].id, tenant_id=seed["tenant"].id
        )

        await task_service.complete_task(
            db, task_id=qa_task.id, user_id=seed["qa_staff"].id,
            tenant_id=seed["tenant"].id, indexed_data={"title": "Corrected by QA"},
        )

        updated = await db.get(Record, record.id)
        assert updated.indexed_data == {"title": "Corrected by QA"}
        assert updated.current_version == 3

        versions = (await db.execute(
            sa_select(RecordVersion).where(RecordVersion.record_id == record.id)
        )).scalars().all()
        assert any(v.indexed_data == {"title": "Corrected by QA"} for v in versions)

    async def test_start_task_wrong_user_forbidden(self, db: AsyncSession, seed):
        from fastapi import HTTPException
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        with pytest.raises(HTTPException) as exc_info:
            await task_service.start_task(
                db, task_id=task.id,
                user_id=seed["indexer2"].id,  # wrong user
                tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 403


class TestDisqualifyTask:
    async def test_disqualify_releases_lock_and_sets_record_status(self, db: AsyncSession, seed):
        from app.models import Record, RecordStatus

        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(
            db, task_id=task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )

        disqualified = await task_service.disqualify_task(
            db, task_id=task.id, user_id=seed["indexer"].id,
            reason="Blank page", tenant_id=seed["tenant"].id,
        )
        assert disqualified.status == TaskStatus.completed

        record = await db.get(Record, seed["record"].id)
        assert record.status == RecordStatus.disqualified
        assert record.locked_by is None

    async def test_disqualify_wrong_user_forbidden(self, db: AsyncSession, seed):
        from fastapi import HTTPException
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        with pytest.raises(HTTPException) as exc_info:
            await task_service.disqualify_task(
                db, task_id=task.id, user_id=seed["indexer2"].id,
                reason="Blank page", tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 403

    async def test_disqualify_non_indexing_task_rejected(self, db: AsyncSession, seed):
        from fastapi import HTTPException
        qa_task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.qa, agent_id=seed["qa_staff"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        with pytest.raises(HTTPException) as exc_info:
            await task_service.disqualify_task(
                db, task_id=qa_task.id, user_id=seed["qa_staff"].id,
                reason="N/A", tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_batch_advances_to_qa_with_mix_of_indexed_and_disqualified(self, db: AsyncSession, seed):
        """A disqualified record must not block the batch from advancing,
        and must not get a QA task created for it (nothing was indexed)."""
        from app.models import Batch, BatchStatus, Record, RecordStatus, Task as TaskModel

        task1 = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        task2 = await task_service.assign_task(
            db, record_id=seed["record2"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        await task_service.start_task(
            db, task_id=task1.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        await task_service.complete_task(
            db, task_id=task1.id, user_id=seed["indexer"].id,
            tenant_id=seed["tenant"].id, indexed_data={"title": "Indexed"},
        )

        await task_service.start_task(
            db, task_id=task2.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        await task_service.disqualify_task(
            db, task_id=task2.id, user_id=seed["indexer"].id,
            reason="Unreadable", tenant_id=seed["tenant"].id,
        )

        batch = await db.get(Batch, seed["batch"].id)
        assert batch.status == BatchStatus.qa_review

        record2 = await db.get(Record, seed["record2"].id)
        assert record2.status == RecordStatus.disqualified

        qa_tasks = (await db.execute(
            select(TaskModel).where(
                TaskModel.batch_id == seed["batch"].id,
                TaskModel.task_type == TaskType.qa,
            )
        )).scalars().all()
        assert len(qa_tasks) == 1
        assert qa_tasks[0].record_id == seed["record"].id


class TestBulkReassign:
    async def test_bulk_reassign_two_tasks(self, db: AsyncSession, seed):
        t1 = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        t2 = await task_service.assign_task(
            db, record_id=seed["record2"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        updated = await task_service.bulk_reassign(
            db, task_ids=[t1.id, t2.id],
            new_agent_id=seed["indexer2"].id,
            supervisor_id=seed["supervisor"].id,
            tenant_id=seed["tenant"].id,
        )
        assert all(t.assigned_to == seed["indexer2"].id for t in updated)
        assert all(t.status == TaskStatus.pending for t in updated)

    async def test_bulk_reassign_via_api(self, client, seed, db):
        t1 = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        t2 = await task_service.assign_task(
            db, record_id=seed["record2"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.commit()

        sup_token = token(seed["supervisor"])
        resp = await client.post(
            "/api/tasks/bulk-reassign",
            json={"task_ids": [t1.id, t2.id], "agent_id": seed["indexer2"].id},
            headers={"Authorization": f"Bearer {sup_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert all(t["assigned_to"] == seed["indexer2"].id for t in data)


class TestStaleTaskDetection:
    async def test_get_stale_tasks_returns_overdue(self, db: AsyncSession, seed):
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        # Backdate due_at to the past to simulate a stale task
        task.due_at = datetime.utcnow() - timedelta(hours=1)
        await db.flush()

        stale = await task_service.get_stale_tasks(
            db, project_id=seed["project"].id, tenant_id=seed["tenant"].id
        )
        assert any(t.id == task.id for t in stale)

    async def test_non_overdue_task_not_in_stale_list(self, db: AsyncSession, seed):
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        # due_at defaults to now + stale_threshold (8h) → not stale yet
        stale = await task_service.get_stale_tasks(
            db, project_id=seed["project"].id, tenant_id=seed["tenant"].id
        )
        assert not any(t.id == task.id for t in stale)
