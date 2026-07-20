"""Shift-role bucket assignment and indexer/QA task-assignment enforcement."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskType
from app.models.shift import ShiftRole, UserProjectAssignment
from app.services import batch_service, cabinet_service, staff_assignment_service, task_service


async def _assignment_for(db: AsyncSession, *, user_id: int, project_id: int) -> UserProjectAssignment:
    result = await db.execute(
        select(UserProjectAssignment).where(
            UserProjectAssignment.user_id == user_id,
            UserProjectAssignment.project_id == project_id,
        )
    )
    return result.scalar_one()


class TestStaffBuckets:
    async def test_get_staff_buckets_groups_by_shift_role(self, db: AsyncSession, seed):
        buckets = await staff_assignment_service.get_staff_buckets(
            db, project_id=seed["project"].id, shift_id=seed["shift"].id, tenant_id=seed["tenant"].id,
        )
        indexer_ids = {m["user_id"] for m in buckets["indexer"]}
        qa_ids = {m["user_id"] for m in buckets["qa"]}
        assert {seed["indexer"].id, seed["indexer2"].id} == indexer_ids
        assert {seed["qa_staff"].id} == qa_ids
        assert buckets["unassigned"] == []

    async def test_has_active_work_false_when_no_tasks(self, db: AsyncSession, seed):
        assert not await staff_assignment_service.has_active_work(
            db, user_id=seed["indexer"].id, project_id=seed["project"].id,
        )

    async def test_has_active_work_true_with_pending_task(self, db: AsyncSession, seed):
        await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        assert await staff_assignment_service.has_active_work(
            db, user_id=seed["indexer"].id, project_id=seed["project"].id,
        )

    async def test_move_user_bucket_happy_path(self, db: AsyncSession, seed):
        assignment = await _assignment_for(db, user_id=seed["indexer"].id, project_id=seed["project"].id)
        moved = await staff_assignment_service.move_user_bucket(
            db, assignment_id=assignment.id, new_shift_role=ShiftRole.qa,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert moved.shift_role == ShiftRole.qa

    async def test_move_user_bucket_to_unassigned(self, db: AsyncSession, seed):
        assignment = await _assignment_for(db, user_id=seed["qa_staff"].id, project_id=seed["project"].id)
        moved = await staff_assignment_service.move_user_bucket(
            db, assignment_id=assignment.id, new_shift_role=None,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert moved.shift_role is None

    async def test_move_user_bucket_blocked_by_active_work(self, db: AsyncSession, seed):
        await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        assignment = await _assignment_for(db, user_id=seed["indexer"].id, project_id=seed["project"].id)
        with pytest.raises(HTTPException) as exc_info:
            await staff_assignment_service.move_user_bucket(
                db, assignment_id=assignment.id, new_shift_role=ShiftRole.qa,
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 409

    async def test_move_user_bucket_wrong_tenant_404(self, db: AsyncSession, seed):
        assignment = await _assignment_for(db, user_id=seed["indexer"].id, project_id=seed["project"].id)
        with pytest.raises(HTTPException) as exc_info:
            await staff_assignment_service.move_user_bucket(
                db, assignment_id=assignment.id, new_shift_role=ShiftRole.qa,
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id + 999,
            )
        assert exc_info.value.status_code == 404


class TestShiftRoleEnforcement:
    async def test_assign_task_rejects_wrong_shift_role(self, db: AsyncSession, seed):
        with pytest.raises(HTTPException) as exc_info:
            await task_service.assign_task(
                db, record_id=seed["record"].id, batch_id=seed["batch"].id,
                task_type=TaskType.qa, agent_id=seed["indexer"].id,  # indexer, not qa
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_assign_task_accepts_correct_shift_role(self, db: AsyncSession, seed):
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.qa, agent_id=seed["qa_staff"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert task.assigned_to == seed["qa_staff"].id

    async def test_assign_task_qc_bypasses_shift_role_check(self, db: AsyncSession, seed):
        # qc_agent has no UserProjectAssignment/shift_role at all — must still work
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.qc, agent_id=seed["qc_agent"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert task.assigned_to == seed["qc_agent"].id

    async def test_reassign_task_rejects_wrong_shift_role(self, db: AsyncSession, seed):
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        with pytest.raises(HTTPException) as exc_info:
            await task_service.reassign_task(
                db, task_id=task.id, new_agent_id=seed["qa_staff"].id,  # qa, not indexer
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_reassign_task_accepts_correct_shift_role(self, db: AsyncSession, seed):
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        reassigned = await task_service.reassign_task(
            db, task_id=task.id, new_agent_id=seed["indexer2"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert reassigned.assigned_to == seed["indexer2"].id

    async def test_create_indexing_batch_rejects_non_indexer(self, db: AsyncSession, seed):
        from app.models.cabinet import Cabinet
        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Test Cabinet")
        db.add(cabinet)
        await db.flush()

        with pytest.raises(HTTPException) as exc_info:
            await cabinet_service.create_indexing_batch(
                db, cabinet_id=cabinet.id, project_id=seed["project"].id,
                document_type_id=seed["doc_type"].id, record_ids=[seed["record"].id],
                agent_id=seed["qa_staff"].id,  # qa, not indexer
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_create_indexing_batch_rejects_already_batched_record(self, db: AsyncSession, seed):
        from app.models.cabinet import Cabinet
        from app.models.record import Record, RecordStatus
        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Test Cabinet")
        db.add(cabinet)
        await db.flush()
        # Already parented to seed["batch"], still "pending" (record.status
        # only advances once the indexer starts the task) — must not be
        # eligible for a second batch.
        already_batched = Record(
            cabinet_id=cabinet.id, batch_id=seed["batch"].id,
            status=RecordStatus.pending, current_version=1,
        )
        db.add(already_batched)
        await db.flush()

        with pytest.raises(HTTPException) as exc_info:
            await cabinet_service.create_indexing_batch(
                db, cabinet_id=cabinet.id, project_id=seed["project"].id,
                document_type_id=seed["doc_type"].id, record_ids=[already_batched.id],
                agent_id=seed["indexer"].id,
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400
        assert already_batched.batch_id == seed["batch"].id  # unchanged

    async def test_create_indexing_batch_rejects_non_pending_record(self, db: AsyncSession, seed):
        from app.models.cabinet import Cabinet
        from app.models.record import Record, RecordStatus
        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Test Cabinet")
        db.add(cabinet)
        await db.flush()
        indexed_record = Record(cabinet_id=cabinet.id, status=RecordStatus.indexed, current_version=1)
        db.add(indexed_record)
        await db.flush()

        with pytest.raises(HTTPException) as exc_info:
            await cabinet_service.create_indexing_batch(
                db, cabinet_id=cabinet.id, project_id=seed["project"].id,
                document_type_id=seed["doc_type"].id, record_ids=[indexed_record.id],
                agent_id=seed["indexer"].id,
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_create_indexing_batch_accepts_pending_unbatched_record(self, db: AsyncSession, seed):
        from app.models.cabinet import Cabinet
        from app.models.record import Record, RecordStatus
        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Test Cabinet")
        db.add(cabinet)
        await db.flush()
        eligible = Record(cabinet_id=cabinet.id, status=RecordStatus.pending, current_version=1)
        db.add(eligible)
        await db.flush()

        new_batch = await cabinet_service.create_indexing_batch(
            db, cabinet_id=cabinet.id, project_id=seed["project"].id,
            document_type_id=seed["doc_type"].id, record_ids=[eligible.id],
            agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        assert eligible.batch_id == new_batch.id

    async def test_create_indexing_batch_rejects_empty_record_ids(self, db: AsyncSession, seed):
        from app.models.cabinet import Cabinet
        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Test Cabinet")
        db.add(cabinet)
        await db.flush()

        with pytest.raises(HTTPException) as exc_info:
            await cabinet_service.create_indexing_batch(
                db, cabinet_id=cabinet.id, project_id=seed["project"].id,
                document_type_id=seed["doc_type"].id, record_ids=[],
                agent_id=seed["indexer"].id,
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

        from app.models import Batch
        result = await db.execute(select(Batch).where(Batch.cabinet_id == cabinet.id))
        assert result.scalars().first() is None

    async def test_assign_qa_agent_rejects_non_qa(self, db: AsyncSession, seed):
        from app.models import BatchStatus
        seed["batch"].status = BatchStatus.qa_review
        await db.flush()
        with pytest.raises(HTTPException) as exc_info:
            await batch_service.assign_qa_agent(
                db, batch_id=seed["batch"].id,
                agent_id=seed["indexer"].id,  # indexer, not qa
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_assign_qa_agent_accepts_qa_staff(self, db: AsyncSession, seed):
        from app.models import BatchStatus
        task = Task(
            record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.qa, status="pending",
        )
        db.add(task)
        seed["batch"].status = BatchStatus.qa_review
        await db.flush()
        batch = await batch_service.assign_qa_agent(
            db, batch_id=seed["batch"].id, agent_id=seed["qa_staff"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert batch.id == seed["batch"].id
