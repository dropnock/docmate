"""Batch state machine, AQL integration, and batch API tests."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Batch, BatchStatus, Record, RecordStatus, Task, TaskType
from app.services import batch_service
from tests.conftest import auth_headers


class TestBatchStateMachine:
    async def test_submit_batch_transitions_to_submitted(self, db: AsyncSession, seed):
        batch = seed["batch"]
        batch.status = BatchStatus.draft
        await db.flush()

        result = await batch_service.submit_batch(
            db,
            batch_id=batch.id,
            supervisor_id=seed["supervisor"].id,
            tenant_id=seed["tenant"].id,
        )
        assert result.status == BatchStatus.submitted

    async def test_submit_non_draft_batch_raises_400(self, db: AsyncSession, seed):
        from fastapi import HTTPException
        # batch is already 'indexing' in seed
        with pytest.raises(HTTPException) as exc:
            await batch_service.submit_batch(
                db,
                batch_id=seed["batch"].id,
                supervisor_id=seed["supervisor"].id,
                tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 400

    async def test_advance_to_indexing(self, db: AsyncSession, seed):
        batch = seed["batch"]
        batch.status = BatchStatus.submitted
        await db.flush()

        result = await batch_service.advance_to_indexing(
            db,
            batch_id=batch.id,
            supervisor_id=seed["supervisor"].id,
            tenant_id=seed["tenant"].id,
        )
        assert result.status == BatchStatus.indexing

    async def test_advance_to_indexing_from_wrong_state_raises(self, db: AsyncSession, seed):
        from fastapi import HTTPException
        # batch is 'indexing', not 'submitted'
        with pytest.raises(HTTPException) as exc:
            await batch_service.advance_to_indexing(
                db,
                batch_id=seed["batch"].id,
                supervisor_id=seed["supervisor"].id,
                tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 400

    async def test_advance_to_qa_review(self, db: AsyncSession, seed):
        # seed batch is already 'indexing'
        result = await batch_service.advance_to_qa(
            db,
            batch_id=seed["batch"].id,
            supervisor_id=seed["supervisor"].id,
            tenant_id=seed["tenant"].id,
        )
        assert result.status == BatchStatus.qa_review

    async def test_advance_to_customer_qc_computes_sample_size(self, db: AsyncSession, seed):
        batch = seed["batch"]
        batch.status = BatchStatus.qa_review
        batch.aql_level_snapshot = 1.5
        await db.flush()

        result = await batch_service.advance_to_customer_qc(
            db,
            batch_id=batch.id,
            supervisor_id=seed["supervisor"].id,
            tenant_id=seed["tenant"].id,
        )
        assert result.status == BatchStatus.customer_qc
        # 2 records → AQL 1.5 → sample_size should be small but > 0
        assert result.aql_sample_size is not None
        assert result.aql_sample_size > 0

    async def test_state_machine_full_chain(self, db: AsyncSession, seed):
        """Walk draft → submitted → indexing → qa_review → customer_qc."""
        batch = seed["batch"]
        batch.status = BatchStatus.draft
        await db.flush()

        await batch_service.submit_batch(
            db, batch_id=batch.id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert batch.status == BatchStatus.submitted

        await batch_service.advance_to_indexing(
            db, batch_id=batch.id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert batch.status == BatchStatus.indexing

        await batch_service.advance_to_qa(
            db, batch_id=batch.id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert batch.status == BatchStatus.qa_review

        batch.aql_level_snapshot = 1.5
        await batch_service.advance_to_customer_qc(
            db, batch_id=batch.id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert batch.status == BatchStatus.customer_qc


class TestAutoAdvanceToQA:
    async def test_auto_advance_creates_qa_tasks(self, db: AsyncSession, seed):
        """When all records are indexed, auto_advance_to_qa creates one QA task per record."""
        batch = seed["batch"]
        # batch is already 'indexing'
        result = await batch_service.auto_advance_to_qa(
            db, batch_id=batch.id, tenant_id=seed["tenant"].id,
        )
        assert result.status == BatchStatus.qa_review

        tasks = (await db.execute(
            select(Task).where(Task.batch_id == batch.id, Task.task_type == TaskType.qa)
        )).scalars().all()
        assert len(tasks) == 2  # one per record in the batch

    async def test_auto_advance_noop_if_not_indexing(self, db: AsyncSession, seed):
        batch = seed["batch"]
        batch.status = BatchStatus.qa_review
        await db.flush()

        result = await batch_service.auto_advance_to_qa(
            db, batch_id=batch.id, tenant_id=seed["tenant"].id,
        )
        # Should return without changing state (already qa_review)
        assert result.status == BatchStatus.qa_review

        tasks = (await db.execute(
            select(Task).where(Task.batch_id == batch.id, Task.task_type == TaskType.qa)
        )).scalars().all()
        assert len(tasks) == 0  # no tasks created since we didn't re-enter indexing→qa


class TestCustomerRejection:
    async def test_reject_record_creates_version_and_marks_qc_failed(self, db: AsyncSession, seed):
        from sqlalchemy import select
        from app.models import RecordVersion

        record = seed["record"]
        record.indexed_data = {"title": "Bad Data"}
        await db.flush()

        await batch_service.reject_record_by_customer(
            db,
            record_id=record.id,
            user_id=seed["qc_agent"].id,
            tenant_id=seed["tenant"].id,
            reason="Wrong field value",
        )
        await db.flush()

        assert record.status == RecordStatus.qc_failed

        versions = (await db.execute(
            select(RecordVersion).where(RecordVersion.record_id == record.id)
        )).scalars().all()
        assert len(versions) == 1
        assert versions[0].indexed_data == {"title": "Bad Data"}

    async def test_reject_nonexistent_record_raises_404(self, db: AsyncSession, seed):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await batch_service.reject_record_by_customer(
                db, record_id=99999,
                user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 404


class TestBatchAuditTrail:
    async def test_submit_writes_audit_event(self, db: AsyncSession, seed):
        batch = seed["batch"]
        batch.status = BatchStatus.draft
        await db.flush()

        await batch_service.submit_batch(
            db, batch_id=batch.id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        events = (await db.execute(
            select(AuditLog).where(AuditLog.entity_id == batch.id)
        )).scalars().all()
        assert len(events) >= 1
        statuses = [e.new_value.get("status") for e in events if e.new_value]
        assert "submitted" in statuses


class TestBatchAPI:
    async def test_list_batches_for_project(self, client, seed):
        resp = await client.get(
            f"/api/projects/{seed['project'].id}/batches",
            headers=auth_headers(seed["supervisor"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(b["id"] == seed["batch"].id for b in data)

    async def test_get_batch_detail(self, client, seed):
        resp = await client.get(
            f"/api/batches/{seed['batch'].id}",
            headers=auth_headers(seed["supervisor"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == seed["batch"].id
        assert data["name"] == "Batch 001"

    async def test_get_batch_records(self, client, seed):
        resp = await client.get(
            f"/api/batches/{seed['batch'].id}/records",
            headers=auth_headers(seed["indexer"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    async def test_get_nonexistent_batch_returns_404(self, client, seed):
        resp = await client.get(
            "/api/batches/99999",
            headers=auth_headers(seed["supervisor"]),
        )
        assert resp.status_code == 404
