"""Supervisor record requeue (scripts/../services/record_service.py:
requeue_record, bulk_requeue_records) — send a record back for re-indexing
or a fresh internal QA pass, outside the normal per-task workflow."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Batch, BatchStatus, Cabinet, Record, RecordStatus, Task, TaskStatus, TaskType
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.record_version import RecordVersion, VersionReason
from app.services import lock_service, record_service, task_service
from tests.conftest import token


class TestRequeueRecordToIndexing:
    async def test_resets_to_pending_and_detaches_batch_with_version_and_audit(
        self, db: AsyncSession, seed
    ):
        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Cabinet 1")
        db.add(cabinet)
        await db.flush()

        record = seed["record"]
        record.cabinet_id = cabinet.id
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

        # Detached back into the same unbatched-pending pool
        # cabinet_service.create_indexing_batch already serves — no rework
        # batch/task, since assignment happens later via the existing
        # "allocate pending records to indexers" flow.
        assert result.status == RecordStatus.pending
        assert result.batch_id is None
        assert original_batch_id is not None

        open_tasks = (await db.execute(
            select(Task).where(
                Task.record_id == record.id,
                Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
            )
        )).scalars().all()
        assert open_tasks == []

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
        assert audit.old_value == {"status": "qa_passed"}
        assert audit.new_value == {"status": "pending"}
        assert audit.metadata_ == {"note": "Illegible field"}

    async def test_requires_cabinet_id(self, db: AsyncSession, seed):
        # seed's record has no cabinet_id — it can never appear in the
        # unbatched-pending pool this target relies on.
        record = seed["record"]
        assert record.cabinet_id is None

        with pytest.raises(HTTPException) as exc_info:
            await record_service.requeue_record(
                db, record_id=record.id, target="indexing",
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_force_unlocks_a_lock_held_by_another_user(self, db: AsyncSession, seed):
        record = seed["record"]
        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Cabinet 1")
        db.add(cabinet)
        await db.flush()
        record.cabinet_id = cabinet.id
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
        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Cabinet 1")
        db.add(cabinet)
        await db.flush()
        record.cabinet_id = cabinet.id
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
    async def test_qa_target_creates_one_rework_batch_per_record(self, db: AsyncSession, seed):
        results = await record_service.bulk_requeue_records(
            db, record_ids=[seed["record"].id, seed["record2"].id], target="qa",
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        assert len({r.batch_id for r in results}) == 2

    async def test_indexing_target_detaches_all_records_to_pending(self, db: AsyncSession, seed):
        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Cabinet 1")
        db.add(cabinet)
        await db.flush()
        seed["record"].cabinet_id = cabinet.id
        seed["record2"].cabinet_id = cabinet.id
        await db.flush()

        results = await record_service.bulk_requeue_records(
            db, record_ids=[seed["record"].id, seed["record2"].id], target="indexing",
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        assert all(r.status == RecordStatus.pending for r in results)
        assert all(r.batch_id is None for r in results)


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
