"""Batch-level QA assignment from the Cabinet Assignment page: the chosen
agent must actually end up with the batch's pending QA tasks (and see them
in GET /tasks/mine), reassignment must move them, and a request that changes
nothing must say so instead of reporting success."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BatchStatus, Task, TaskStatus, TaskType, User, UserRole, Portal
from app.models.shift import ShiftRole, UserProjectAssignment
from app.services import batch_service
from tests.conftest import token


async def _qa_batch(db: AsyncSession, seed, *, assigned_to=None, status=TaskStatus.pending):
    seed["batch"].status = BatchStatus.qa_review
    tasks = [
        Task(record_id=r.id, batch_id=seed["batch"].id, task_type=TaskType.qa,
             status=status, assigned_to=assigned_to)
        for r in (seed["record"], seed["record2"])
    ]
    db.add_all(tasks)
    await db.flush()
    return tasks


async def _second_qa_agent(db: AsyncSession, seed) -> User:
    qa2 = User(
        tenant_id=seed["tenant"].id, organization_id=seed["de_org"].id, email="qa2@test.com",
        keycloak_sub="sub-qa2", full_name="QA Two",
        role=UserRole.de_staff, portal=Portal.digitizing, is_active=True,
    )
    db.add(qa2)
    await db.flush()
    db.add(UserProjectAssignment(
        user_id=qa2.id, project_id=seed["project"].id, shift_id=seed["shift"].id,
        shift_role=ShiftRole.qa, is_active=True,
    ))
    await db.flush()
    return qa2


async def _assign(db, seed, agent_id):
    return await batch_service.assign_qa_agent(
        db, batch_id=seed["batch"].id, agent_id=agent_id,
        supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )


class TestAssignQaAgent:
    async def test_assigns_unassigned_tasks(self, db: AsyncSession, seed):
        tasks = await _qa_batch(db, seed)
        _, count = await _assign(db, seed, seed["qa_staff"].id)
        assert count == 2
        assert all(t.assigned_to == seed["qa_staff"].id for t in tasks)

    async def test_reassigns_pending_tasks_to_new_agent(self, db: AsyncSession, seed):
        tasks = await _qa_batch(db, seed, assigned_to=seed["qa_staff"].id)
        qa2 = await _second_qa_agent(db, seed)
        _, count = await _assign(db, seed, qa2.id)
        assert count == 2
        assert all(t.assigned_to == qa2.id for t in tasks)

    async def test_leaves_in_progress_tasks_alone(self, db: AsyncSession, seed):
        tasks = await _qa_batch(db, seed, assigned_to=seed["qa_staff"].id)
        tasks[0].status = TaskStatus.in_progress
        await db.flush()
        qa2 = await _second_qa_agent(db, seed)
        _, count = await _assign(db, seed, qa2.id)
        assert count == 1
        assert tasks[0].assigned_to == seed["qa_staff"].id
        assert tasks[1].assigned_to == qa2.id

    async def test_nothing_to_assign_raises_409(self, db: AsyncSession, seed):
        await _qa_batch(db, seed, assigned_to=seed["qa_staff"].id)
        with pytest.raises(HTTPException) as exc_info:
            await _assign(db, seed, seed["qa_staff"].id)  # already theirs
        assert exc_info.value.status_code == 409

    async def test_completed_tasks_raise_409(self, db: AsyncSession, seed):
        await _qa_batch(db, seed, assigned_to=seed["qa_staff"].id, status=TaskStatus.completed)
        qa2 = await _second_qa_agent(db, seed)
        with pytest.raises(HTTPException) as exc_info:
            await _assign(db, seed, qa2.id)
        assert exc_info.value.status_code == 409


class TestAssignQaAgentApi:
    async def test_assigned_agent_sees_tasks_and_batch_lists_them(self, client, db: AsyncSession, seed):
        await _qa_batch(db, seed)
        await db.commit()
        sup = {"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": "digitizing"}

        resp = await client.patch(
            f"/api/cabinets/batches/{seed['batch'].id}/assign-qa",
            json={"agent_id": seed["qa_staff"].id}, headers=sup,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["assigned_count"] == 2

        # The QA agent's My Tasks now has both records.
        qa = {"Authorization": f"Bearer {token(seed['qa_staff'])}", "X-Portal": "digitizing"}
        mine = (await client.get("/api/tasks/mine", headers=qa)).json()
        assert sorted(t["record_id"] for t in mine if t["task_type"] == "qa") == sorted(
            [seed["record"].id, seed["record2"].id]
        )

        # And the Cabinet Assignment page can show who has the batch after a refresh.
        batches = (await client.get(
            f"/api/projects/{seed['project'].id}/batches", headers=sup,
        )).json()
        row = next(b for b in batches if b["id"] == seed["batch"].id)
        assert row["qa_agent_name"] == "QA Staff"

        # Assigning the same agent again changes nothing → 409, not a fake success.
        again = await client.patch(
            f"/api/cabinets/batches/{seed['batch'].id}/assign-qa",
            json={"agent_id": seed["qa_staff"].id}, headers=sup,
        )
        assert again.status_code == 409

        tasks = (await db.execute(select(Task).where(Task.batch_id == seed["batch"].id))).scalars().all()
        assert all(t.assigned_to == seed["qa_staff"].id for t in tasks)
