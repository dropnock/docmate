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


class TestSkipTask:
    async def test_skip_releases_lock_and_sets_record_status(self, db: AsyncSession, seed):
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

        skipped = await task_service.skip_task(
            db, task_id=task.id, user_id=seed["indexer"].id,
            status=RecordStatus.withdrawn, tenant_id=seed["tenant"].id,
        )
        assert skipped.status == TaskStatus.completed

        record = await db.get(Record, seed["record"].id)
        assert record.status == RecordStatus.withdrawn
        assert record.locked_by is None

    async def test_skip_as_ineligible_sets_record_status(self, db: AsyncSession, seed):
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

        skipped = await task_service.skip_task(
            db, task_id=task.id, user_id=seed["indexer"].id,
            status=RecordStatus.ineligible, tenant_id=seed["tenant"].id,
        )
        assert skipped.status == TaskStatus.completed

        record = await db.get(Record, seed["record"].id)
        assert record.status == RecordStatus.ineligible

    async def test_skip_rejects_disallowed_status(self, db: AsyncSession, seed):
        from fastapi import HTTPException
        from app.models import RecordStatus

        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(
            db, task_id=task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        with pytest.raises(HTTPException) as exc_info:
            await task_service.skip_task(
                db, task_id=task.id, user_id=seed["indexer"].id,
                status=RecordStatus.qa_passed, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_skip_wrong_user_forbidden(self, db: AsyncSession, seed):
        from fastapi import HTTPException
        from app.models import RecordStatus
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        with pytest.raises(HTTPException) as exc_info:
            await task_service.skip_task(
                db, task_id=task.id, user_id=seed["indexer2"].id,
                status=RecordStatus.withdrawn, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 403

    async def test_skip_non_indexing_task_rejected(self, db: AsyncSession, seed):
        from fastapi import HTTPException
        from app.models import RecordStatus
        qa_task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.qa, agent_id=seed["qa_staff"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        with pytest.raises(HTTPException) as exc_info:
            await task_service.skip_task(
                db, task_id=qa_task.id, user_id=seed["qa_staff"].id,
                status=RecordStatus.withdrawn, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_completing_last_record_does_not_auto_advance_batch(self, db: AsyncSession, seed):
        """Indexing/skipping every record in the batch no longer auto-advances
        it to qa_review — the indexer must press Complete Batch (see
        TestCompleteIndexingBatch below). The record stays visible in their
        list (task.status == completed, but nothing hides it) rather than
        disappearing, and no QA tasks get created until then."""
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
        completed1 = await task_service.complete_task(
            db, task_id=task1.id, user_id=seed["indexer"].id,
            tenant_id=seed["tenant"].id, indexed_data={"title": "Indexed"},
        )

        await task_service.start_task(
            db, task_id=task2.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        await task_service.skip_task(
            db, task_id=task2.id, user_id=seed["indexer"].id,
            status=RecordStatus.ineligible, tenant_id=seed["tenant"].id,
        )

        batch = await db.get(Batch, seed["batch"].id)
        assert batch.status == BatchStatus.indexing

        record2 = await db.get(Record, seed["record2"].id)
        assert record2.status == RecordStatus.ineligible
        # Both tasks are "completed" — that's what makes the record
        # reopenable rather than hidden — but the batch hasn't moved on.
        assert completed1.status == TaskStatus.completed

        qa_tasks = (await db.execute(
            select(TaskModel).where(
                TaskModel.batch_id == seed["batch"].id,
                TaskModel.task_type == TaskType.qa,
            )
        )).scalars().all()
        assert qa_tasks == []

    async def test_reopening_completed_indexing_task_creates_correction_version(
        self, db: AsyncSession, seed
    ):
        """Reopening (start_task again) and resubmitting an already-indexed
        record — before the batch is completed — versions the change as a
        "correction", not "rework_after_qa" (that's reserved for the actual
        post-QA-rejection resubmission)."""
        from app.models import Record, RecordVersion
        from app.models.record_version import VersionReason

        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(
            db, task_id=task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        await task_service.complete_task(
            db, task_id=task.id, user_id=seed["indexer"].id,
            tenant_id=seed["tenant"].id, indexed_data={"title": "First pass"},
        )

        # Reopen the same (already-completed) task and resubmit a correction.
        reopened = await task_service.start_task(
            db, task_id=task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        assert reopened.status == TaskStatus.in_progress
        record = await db.get(Record, seed["record"].id)
        assert record.locked_by == seed["indexer"].id

        await task_service.complete_task(
            db, task_id=task.id, user_id=seed["indexer"].id,
            tenant_id=seed["tenant"].id, indexed_data={"title": "Fixed typo"},
        )

        record = await db.get(Record, seed["record"].id)
        assert record.indexed_data == {"title": "Fixed typo"}
        assert record.current_version == 3  # two submissions: 1 -> 2 -> 3

        versions = (await db.execute(
            select(RecordVersion)
            .where(RecordVersion.record_id == record.id)
            .order_by(RecordVersion.version_number)
        )).scalars().all()
        assert versions[-1].reason == VersionReason.correction


class TestCompleteIndexingBatch:
    async def test_blocked_while_records_incomplete(self, db: AsyncSession, seed):
        from fastapi import HTTPException
        from app.services import batch_service

        task1 = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await task_service.assign_task(
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
        # record2 still pending — nothing done with it yet.

        with pytest.raises(HTTPException) as exc_info:
            await batch_service.complete_indexing_batch(
                db, batch_id=seed["batch"].id, user_id=seed["indexer"].id,
                tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400
        assert "1 record" in exc_info.value.detail

        from app.models import Batch, BatchStatus
        batch = await db.get(Batch, seed["batch"].id)
        assert batch.status == BatchStatus.indexing

    async def test_succeeds_once_all_records_terminal(self, db: AsyncSession, seed):
        from app.models import Batch, BatchStatus, RecordStatus, Task as TaskModel
        from app.services import batch_service

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
        await task_service.skip_task(
            db, task_id=task2.id, user_id=seed["indexer"].id,
            status=RecordStatus.excluded, tenant_id=seed["tenant"].id,
        )

        batch = await batch_service.complete_indexing_batch(
            db, batch_id=seed["batch"].id, user_id=seed["indexer"].id,
            tenant_id=seed["tenant"].id,
        )
        assert batch.status == BatchStatus.qa_review

        qa_tasks = (await db.execute(
            select(TaskModel).where(
                TaskModel.batch_id == seed["batch"].id,
                TaskModel.task_type == TaskType.qa,
            )
        )).scalars().all()
        assert len(qa_tasks) == 1
        assert qa_tasks[0].record_id == seed["record"].id


class TestSkipTaskExcluded:
    async def test_skip_as_excluded_sets_record_status(self, db: AsyncSession, seed):
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

        skipped = await task_service.skip_task(
            db, task_id=task.id, user_id=seed["indexer"].id,
            status=RecordStatus.excluded, tenant_id=seed["tenant"].id,
        )
        assert skipped.status == TaskStatus.completed

        record = await db.get(Record, seed["record"].id)
        assert record.status == RecordStatus.excluded
        assert record.locked_by is None


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
