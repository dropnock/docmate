"""Customer QC reviewers keep their QC batch open until they submit it: they
can go back to a reviewed record and review it again (task_service.start_task
reopens it), and the lot only moves on to the supervisor once every QC batch
is submitted (lot_service.submit_qc_batch)."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.batch import Batch, BatchStatus, BatchType
from app.models.lot import LotStatus
from app.models.qc_field_result import QcFieldResult
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.services import lot_service, task_service
from tests.conftest import decide, token
from tests.test_defect_classes import TestCustomerQc

QC = TestCustomerQc()
bad = TestCustomerQc._bad


async def _reviewed(db, seed, defects):
    """Every sampled record reviewed, the QC batch not yet submitted."""
    lot, _ = await QC._sample(db, seed)
    await QC._review(db, seed, lot, defects, decide=False)
    tasks = (await db.execute(
        select(Task).join(Batch, Batch.id == Task.batch_id)
        .where(Task.task_type == TaskType.qc, Batch.batch_type == BatchType.qc).order_by(Task.record_id)
    )).scalars().all()
    batch = await db.get(Batch, tasks[0].batch_id)
    return lot, batch, list(tasks)


def _agent(seed):
    agent = seed["qc_agent"]
    agent._tenant_id = seed["tenant"].id
    return agent


def _kw(seed, task):
    return dict(task_id=task.id, user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id)


class TestSubmit:
    async def test_lot_waits_for_the_batch_to_be_submitted(self, db: AsyncSession, seed):
        lot, batch, _ = await _reviewed(db, seed, {0: bad("dob", "typo")})
        assert lot.status == LotStatus.qc_in_progress
        assert batch.status == BatchStatus.indexing

        await lot_service.submit_qc_batch(db, batch=batch, user=_agent(seed), tenant_id=seed["tenant"].id)
        await db.refresh(lot)
        assert batch.status == BatchStatus.complete
        assert lot.status == LotStatus.awaiting_decision

        event = (await db.execute(
            select(AuditLog).where(AuditLog.entity_type == AuditEntityType.batch, AuditLog.entity_id == batch.id)
        )).scalars().all()[-1]
        assert event.action == AuditAction.status_changed
        assert event.performed_by == seed["qc_agent"].id
        assert event.metadata_["passed"] == 12 and event.metadata_["rejected"] == 1

    async def test_every_record_needs_a_review_first(self, db: AsyncSession, seed):
        lot, batch, tasks = await _reviewed(db, seed, {})
        await task_service.start_task(db, **_kw(seed, tasks[3]))
        with pytest.raises(HTTPException) as e:
            await lot_service.submit_qc_batch(db, batch=batch, user=_agent(seed), tenant_id=seed["tenant"].id)
        assert e.value.status_code == 400 and "1 record still needs a review" in e.value.detail
        assert batch.status == BatchStatus.indexing

    async def test_submitting_twice_is_409(self, db: AsyncSession, seed):
        lot, batch, _ = await _reviewed(db, seed, {})
        await lot_service.submit_qc_batch(db, batch=batch, user=_agent(seed), tenant_id=seed["tenant"].id)
        with pytest.raises(HTTPException) as e:
            await lot_service.submit_qc_batch(db, batch=batch, user=_agent(seed), tenant_id=seed["tenant"].id)
        assert e.value.status_code == 409

    async def test_someone_else_cannot_submit(self, db: AsyncSession, seed):
        from app.models.user import Portal, User, UserRole
        lot, batch, _ = await _reviewed(db, seed, {})
        other = User(
            tenant_id=seed["tenant"].id, organization_id=seed["cust_org"].id, email="other-qc@test.com",
            keycloak_sub="sub-other-qc", full_name="Other QC", role=UserRole.customer_qc_agent,
            portal=Portal.customer, is_active=True,
        )
        db.add(other)
        await db.flush()
        other._tenant_id = seed["tenant"].id
        with pytest.raises(HTTPException) as e:
            await lot_service.submit_qc_batch(db, batch=batch, user=other, tenant_id=seed["tenant"].id)
        assert e.value.status_code == 403

    async def test_lot_waits_for_every_batch(self, db: AsyncSession, seed):
        """Two reviewers: the lot moves on only when the second submits."""
        from app.models.document_type import DocumentType
        from app.models.lot import LotRecord
        lot, _ = await QC._sample(db, seed)
        sampled = sorted((await db.execute(
            select(LotRecord.record_id).where(LotRecord.lot_id == lot.id, LotRecord.is_sampled == True)  # noqa: E712
        )).scalars().all())
        doc_type = (await db.execute(
            select(DocumentType).join(Batch, Batch.document_type_id == DocumentType.id)
            .join(Record, Record.batch_id == Batch.id).where(Record.id == sampled[0])
        )).scalar_one()
        from tests.test_aql_router import _make_customer_supervisor
        sup = await _make_customer_supervisor(db, seed)
        first, second = await lot_service.create_qc_batches(
            db, lot_id=lot.id, project_id=lot.project_id, document_type_id=doc_type.id,
            assignments=[
                {"agent_id": seed["qc_agent"].id, "record_ids": sampled[:6]},
                {"agent_id": sup.id, "record_ids": sampled[6:]},
            ],
            supervisor_id=sup.id, tenant_id=seed["tenant"].id,
        )
        accept = [bad("surname", "typo")[1], bad("dob", "typo")[1]]
        for t in (await db.execute(select(Task).where(Task.task_type == TaskType.qc))).scalars():
            await task_service.start_task(db, task_id=t.id, user_id=t.assigned_to, tenant_id=seed["tenant"].id)
            await task_service.complete_task(
                db, task_id=t.id, user_id=t.assigned_to, tenant_id=seed["tenant"].id, field_results=accept,
            )
        await lot_service.submit_qc_batch(db, batch=first, user=_agent(seed), tenant_id=seed["tenant"].id)
        await db.refresh(lot)
        assert lot.status == LotStatus.qc_in_progress
        sup._tenant_id = seed["tenant"].id
        await lot_service.submit_qc_batch(db, batch=second, user=sup, tenant_id=seed["tenant"].id)
        await db.refresh(lot)
        assert lot.status == LotStatus.awaiting_decision


class TestGoBack:
    async def test_reviewed_record_can_be_reviewed_again(self, db: AsyncSession, seed):
        """A rejection the reviewer takes back: the new marks replace the old."""
        lot, batch, tasks = await _reviewed(db, seed, {0: bad("dob", "typo")})
        task = tasks[0]
        assert task.status == TaskStatus.failed

        reopened = await task_service.start_task(db, **_kw(seed, task))
        assert reopened.status == TaskStatus.in_progress
        accept = [bad("surname", "typo")[1], bad("dob", "typo")[1]]
        await task_service.complete_task(db, field_results=accept, **_kw(seed, task))

        record = await db.get(Record, task.record_id)
        assert record.status == RecordStatus.qc_passed
        marks = (await db.execute(select(QcFieldResult).where(QcFieldResult.task_id == task.id))).scalars().all()
        assert sorted((m.field_key, m.status.value) for m in marks) == [("dob", "accepted"), ("surname", "accepted")]

        lot = await decide(db, seed, lot)
        assert lot.status == LotStatus.passed
        assert lot.critical_defect_count == 0

    async def test_pass_can_become_a_rejection(self, db: AsyncSession, seed):
        lot, batch, tasks = await _reviewed(db, seed, {})
        task = tasks[2]
        await task_service.start_task(db, **_kw(seed, task))
        await task_service.fail_task(db, reason="defect", field_results=bad("surname", "missing"), **_kw(seed, task))
        assert (await db.get(Record, task.record_id)).status == RecordStatus.qc_failed
        lot = await decide(db, seed, lot)
        assert lot.status == LotStatus.failed and lot.major_defect_count == 1

    async def test_no_going_back_after_submitting(self, db: AsyncSession, seed):
        lot, batch, tasks = await _reviewed(db, seed, {})
        await lot_service.submit_qc_batch(db, batch=batch, user=_agent(seed), tenant_id=seed["tenant"].id)
        with pytest.raises(HTTPException) as e:
            await task_service.start_task(db, **_kw(seed, tasks[0]))
        assert e.value.status_code == 409


class TestApi:
    def _h(self, user):
        return {"Authorization": f"Bearer {token(user)}", "X-Portal": "customer"}

    async def test_my_tasks_keeps_reviewed_records_until_submitted(self, client, db: AsyncSession, seed):
        lot, batch, tasks = await _reviewed(db, seed, {0: bad("dob", "typo")})
        await db.commit()
        h = self._h(seed["qc_agent"])
        mine = (await client.get("/api/tasks/mine", headers=h)).json()
        assert len(mine) == 13 and {t["batch_status"] for t in mine} == {"indexing"}

        marks = (await client.get(f"/api/tasks/{tasks[0].id}/field-results", headers=h)).json()
        assert {m["field_key"]: (m["status"], m["error_type"]) for m in marks} == {
            "dob": ("defective", "typo"), "surname": ("accepted", None),
        }

        r = await client.post(f"/api/batches/{batch.id}/submit-qc", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "complete"
        assert (await client.get("/api/tasks/mine", headers=h)).json() == []

    async def test_field_results_are_the_reviewers_own(self, client, db: AsyncSession, seed):
        lot, batch, tasks = await _reviewed(db, seed, {})
        await db.commit()
        r = await client.get(f"/api/tasks/{tasks[0].id}/field-results", headers=self._h(seed["indexer"]))
        assert r.status_code == 403
