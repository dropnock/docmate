"""Supervisor record requeue (scripts/../services/record_service.py:
requeue_record, bulk_requeue_records) — send a record back for re-indexing
or a fresh internal QA pass, outside the normal per-task workflow."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Batch, BatchStatus, Record, RecordStatus, Task, TaskStatus, TaskType
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.record_version import RecordVersion, VersionReason
from app.services import lock_service, record_service, task_service
from tests.conftest import token


class TestRequeueRecordToIndexing:
    async def test_creates_rework_batch_task_version_and_audit(self, db: AsyncSession, seed):
        record = seed["record"]
        record.status = RecordStatus.qa_passed
        record.indexed_data = {"title": "Doc A"}
        record.current_version = 1
        await db.flush()

        original_batch_id = record.batch_id

        result = await record_service.requeue_record(
            db, record_id=record.id, target="indexing",
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            note="Illegible field",
        )
        await db.flush()

        assert result.status == RecordStatus.qa_failed
        assert result.batch_id != original_batch_id

        rework_batch = await db.get(Batch, result.batch_id)
        assert rework_batch.status == BatchStatus.indexing
        assert rework_batch.document_type_id == seed["doc_type"].id

        task = (await db.execute(
            select(Task).where(Task.record_id == record.id, Task.task_type == TaskType.indexing)
        )).scalars().all()
        pending_tasks = [t for t in task if t.status == TaskStatus.pending]
        assert len(pending_tasks) == 1
        assert pending_tasks[0].batch_id == rework_batch.id
        assert pending_tasks[0].assigned_to is None

        versions = (await db.execute(
            select(RecordVersion).where(RecordVersion.record_id == record.id)
        )).scalars().all()
        assert len(versions) == 1
        assert versions[0].reason == VersionReason.supervisor_requeue
        assert versions[0].indexed_data == {"title": "Doc A"}
        assert versions[0].created_by == seed["supervisor"].id

        audit = (await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.record,
                AuditLog.entity_id == record.id,
                AuditLog.action == AuditAction.requeued_for_indexing,
            )
        )).scalar_one()
        assert audit.performed_by == seed["supervisor"].id
        assert audit.metadata_ == {"note": "Illegible field"}

    async def test_requires_batch_id(self, db: AsyncSession, seed):
        unbatched = Record(status=RecordStatus.indexed, current_version=1)
        db.add(unbatched)
        await db.flush()

        from fastapi import HTTPException
        import pytest
        with pytest.raises(HTTPException) as exc_info:
            await record_service.requeue_record(
                db, record_id=unbatched.id, target="indexing",
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_force_unlocks_a_lock_held_by_another_user(self, db: AsyncSession, seed):
        record = seed["record"]
        await lock_service.acquire_lock(
            db, record=record, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        await db.flush()
        assert record.locked_by == seed["indexer"].id

        await record_service.requeue_record(
            db, record_id=record.id, target="indexing",
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        assert record.locked_by is None
        unlock_event = (await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.record,
                AuditLog.entity_id == record.id,
                AuditLog.action == AuditAction.unlocked,
            )
        )).scalar_one()
        assert unlock_event.performed_by == seed["supervisor"].id

    async def test_closes_out_an_in_progress_task_first(self, db: AsyncSession, seed):
        record = seed["record"]
        existing_task = await task_service.assign_task(
            db, record_id=record.id, batch_id=record.batch_id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(
            db, task_id=existing_task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        await db.flush()

        await record_service.requeue_record(
            db, record_id=record.id, target="indexing",
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        await db.refresh(existing_task)
        assert existing_task.status == TaskStatus.failed
        assert existing_task.completed_at is not None

        # start_task() already wrote its own pending->in_progress
        # status_changed event for this task — this one, in_progress->failed,
        # is the one requeue_record's task-closing loop wrote.
        status_events = (await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.task,
                AuditLog.entity_id == existing_task.id,
                AuditLog.action == AuditAction.status_changed,
            )
        )).scalars().all()
        assert any(e.new_value["status"] == "failed" for e in status_events)


class TestRequeueRecordToQa:
    async def test_creates_rework_batch_and_task_with_no_version(self, db: AsyncSession, seed):
        record = seed["record"]
        record.status = RecordStatus.qa_failed
        record.indexed_data = {"title": "Doc A"}
        await db.flush()

        result = await record_service.requeue_record(
            db, record_id=record.id, target="qa",
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        assert result.status == RecordStatus.qa_pending

        rework_batch = await db.get(Batch, result.batch_id)
        assert rework_batch.status == BatchStatus.qa_review

        task = (await db.execute(
            select(Task).where(
                Task.record_id == record.id, Task.task_type == TaskType.qa,
                Task.status == TaskStatus.pending,
            )
        )).scalar_one()
        assert task.batch_id == rework_batch.id

        versions = (await db.execute(
            select(RecordVersion).where(RecordVersion.record_id == record.id)
        )).scalars().all()
        assert versions == []

        audit = (await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.record,
                AuditLog.entity_id == record.id,
                AuditLog.action == AuditAction.requeued_for_qa,
            )
        )).scalar_one()
        assert audit.performed_by == seed["supervisor"].id


class TestBulkRequeueRecords:
    async def test_creates_one_rework_batch_per_record(self, db: AsyncSession, seed):
        results = await record_service.bulk_requeue_records(
            db, record_ids=[seed["record"].id, seed["record2"].id], target="indexing",
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        assert len({r.batch_id for r in results}) == 2


class TestRequeueRouter:
    async def test_non_supervisor_role_gets_403(self, db: AsyncSession, seed, client):
        indexer_token = token(seed["indexer"])
        resp = await client.post(
            "/api/records/requeue",
            json={"record_ids": [seed["record"].id], "target": "indexing"},
            headers={"Authorization": f"Bearer {indexer_token}", "X-Portal": "digitizing"},
        )
        assert resp.status_code == 403

    async def test_cross_tenant_record_gets_404(self, db: AsyncSession, seed, client):
        from app.models import Cabinet, Organization, OrgType, Project, S3BucketStatus, Tenant

        other_tenant = Tenant(name="Other Corp", slug="othercorp")
        db.add(other_tenant)
        await db.flush()
        other_org = Organization(tenant_id=other_tenant.id, name="Other Org", type=OrgType.digitizing_entity)
        db.add(other_org)
        await db.flush()
        other_project = Project(
            tenant_id=other_tenant.id, digitizing_org_id=other_org.id, customer_org_id=other_org.id,
            name="Other Project", s3_bucket_status=S3BucketStatus.ready,
        )
        db.add(other_project)
        await db.flush()
        other_cabinet = Cabinet(tenant_id=other_tenant.id, project_id=other_project.id, name="Other Cabinet")
        db.add(other_cabinet)
        await db.flush()
        other_record = Record(cabinet_id=other_cabinet.id, status=RecordStatus.indexed, current_version=1)
        db.add(other_record)
        await db.flush()
        await db.commit()

        supervisor_token = token(seed["supervisor"])
        resp = await client.post(
            "/api/records/requeue",
            json={"record_ids": [other_record.id], "target": "indexing"},
            headers={"Authorization": f"Bearer {supervisor_token}", "X-Portal": "digitizing"},
        )
        assert resp.status_code == 404
