"""Record locking (pessimistic concurrency) and versioning tests."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Record, RecordVersion, Task, TaskStatus, TaskType
from app.services import lock_service, task_service, version_service
from app.models.record_version import VersionReason
from tests.conftest import auth_headers


class TestRecordLocking:
    async def test_acquire_lock_succeeds_for_free_record(self, db: AsyncSession, seed):
        record = seed["record"]
        await lock_service.acquire_lock(
            db, record=record, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        await db.flush()
        assert record.locked_by == seed["indexer"].id
        assert record.locked_at is not None

    async def test_acquire_lock_same_user_idempotent(self, db: AsyncSession, seed):
        record = seed["record"]
        user_id = seed["indexer"].id
        await lock_service.acquire_lock(db, record=record, user_id=user_id, tenant_id=seed["tenant"].id)
        # Re-acquiring own lock must not raise
        await lock_service.acquire_lock(db, record=record, user_id=user_id, tenant_id=seed["tenant"].id)
        assert record.locked_by == user_id

    async def test_acquire_lock_conflict_raises_409(self, db: AsyncSession, seed, client):
        """Second indexer trying to start the same record gets HTTP 409."""
        # Assign task to indexer1 and start it (acquires lock)
        task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.commit()

        await task_service.start_task(
            db, task_id=task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        await db.commit()

        # Assign task to indexer2 for the SAME record
        task2 = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.indexing, agent_id=seed["indexer2"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.commit()

        # indexer2 tries to start → must get 409 via API
        resp = await client.post(
            f"/api/tasks/{task2.id}/start",
            headers=auth_headers(seed["indexer2"]),
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "locked" in detail.get("message", "").lower()

    async def test_release_lock_frees_record(self, db: AsyncSession, seed):
        record = seed["record"]
        user_id = seed["indexer"].id
        await lock_service.acquire_lock(db, record=record, user_id=user_id, tenant_id=seed["tenant"].id)
        await lock_service.release_lock(db, record=record, user_id=user_id, tenant_id=seed["tenant"].id)
        await db.flush()
        assert record.locked_by is None
        assert record.locked_at is None

    async def test_release_lock_noop_when_unlocked(self, db: AsyncSession, seed):
        """Releasing an already-free record must not raise."""
        record = seed["record"]
        await lock_service.release_lock(
            db, record=record, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id
        )
        assert record.locked_by is None


class TestRecordVersioning:
    async def test_initial_version_created_on_complete(self, db: AsyncSession, seed):
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
            tenant_id=seed["tenant"].id, indexed_data={"title": "Doc A"},
        )
        await db.flush()

        record = await db.get(Record, seed["record"].id)
        assert record.indexed_data == {"title": "Doc A"}
        assert record.current_version == 2  # incremented by create_version

        from sqlalchemy import select
        versions = (await db.execute(
            select(RecordVersion).where(RecordVersion.record_id == record.id)
        )).scalars().all()
        assert len(versions) == 1
        assert versions[0].reason == VersionReason.initial_indexing

    async def test_customer_rejection_creates_version(self, db: AsyncSession, seed):
        """Customer rejection freezes current data as v1 and increments version."""
        record = seed["record"]
        record.indexed_data = {"title": "Original"}
        record.current_version = 1
        await db.flush()

        await version_service.create_version(
            db, record=record, reason=VersionReason.rework_after_customer_rejection,
            user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        assert record.current_version == 2

        from sqlalchemy import select
        versions = (await db.execute(
            select(RecordVersion).where(RecordVersion.record_id == record.id)
        )).scalars().all()
        assert len(versions) == 1
        assert versions[0].indexed_data == {"title": "Original"}
        assert versions[0].reason == VersionReason.rework_after_customer_rejection

    async def test_versions_are_immutable(self, db: AsyncSession, seed):
        """Once a RecordVersion row is written its indexed_data is the snapshot."""
        record = seed["record"]
        record.indexed_data = {"title": "First"}
        await db.flush()

        await version_service.create_version(
            db, record=record, reason=VersionReason.initial_indexing,
            user_id=seed["indexer"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        # Change live data; version must retain original snapshot
        record.indexed_data = {"title": "Second"}
        await db.flush()

        from sqlalchemy import select
        v = (await db.execute(
            select(RecordVersion).where(RecordVersion.record_id == record.id)
        )).scalar_one()
        assert v.indexed_data == {"title": "First"}
