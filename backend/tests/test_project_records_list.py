"""Project-wide, status-filterable record list for supervisor review
(record_service.list_project_records, attach_task_attribution)."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Cabinet, Record, RecordStatus, Task, TaskStatus, TaskType
from app.services import record_service, task_service


class TestListProjectRecords:
    async def test_resolves_project_via_batch_when_no_cabinet(self, db: AsyncSession, seed):
        records, total = await record_service.list_project_records(
            db, project_id=seed["project"].id, statuses=None, limit=100, offset=0,
        )
        assert {r.id for r in records} == {seed["record"].id, seed["record2"].id}
        assert total == 2

    async def test_resolves_project_via_cabinet_for_unassigned_record(self, db: AsyncSession, seed):
        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Intake Cabinet")
        db.add(cabinet)
        await db.flush()
        unassigned = Record(cabinet_id=cabinet.id, status=RecordStatus.pending, current_version=1)
        db.add(unassigned)
        await db.flush()

        records, total = await record_service.list_project_records(
            db, project_id=seed["project"].id, statuses=None, limit=100, offset=0,
        )
        assert unassigned.id in {r.id for r in records}
        assert total == 3

    async def test_status_filter(self, db: AsyncSession, seed):
        seed["record"].status = RecordStatus.qa_failed
        seed["record2"].status = RecordStatus.indexed
        await db.flush()

        records, total = await record_service.list_project_records(
            db, project_id=seed["project"].id, statuses=[RecordStatus.qa_failed], limit=100, offset=0,
        )
        assert {r.id for r in records} == {seed["record"].id}
        assert total == 1

    async def test_pagination(self, db: AsyncSession, seed):
        first_page, total = await record_service.list_project_records(
            db, project_id=seed["project"].id, statuses=None, limit=1, offset=0,
        )
        second_page, total2 = await record_service.list_project_records(
            db, project_id=seed["project"].id, statuses=None, limit=1, offset=1,
        )
        assert len(first_page) == 1
        assert len(second_page) == 1
        assert first_page[0].id != second_page[0].id
        assert total == total2 == 2

    async def test_other_project_scoping(self, db: AsyncSession, seed):
        records, total = await record_service.list_project_records(
            db, project_id=seed["project"].id + 999, statuses=None, limit=100, offset=0,
        )
        assert records == []
        assert total == 0


class TestAttachTaskAttribution:
    async def test_resolves_most_recent_indexer_and_qa_reviewer(self, db: AsyncSession, seed):
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

        qa_task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.qa, agent_id=seed["qa_staff"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(db, task_id=qa_task.id, user_id=seed["qa_staff"].id, tenant_id=seed["tenant"].id)
        await task_service.complete_task(
            db, task_id=qa_task.id, user_id=seed["qa_staff"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        record = await db.get(Record, seed["record"].id)
        await record_service.attach_task_attribution(db, [record])

        assert record.indexed_by_id == seed["indexer"].id
        assert record.indexed_by_name == seed["indexer"].full_name
        assert record.qa_by_id == seed["qa_staff"].id
        assert record.qa_by_name == seed["qa_staff"].full_name

    async def test_no_completed_tasks_yields_none(self, db: AsyncSession, seed):
        record = await db.get(Record, seed["record2"].id)
        await record_service.attach_task_attribution(db, [record])
        assert record.indexed_by_id is None
        assert record.indexed_by_name is None
        assert record.qa_by_id is None
        assert record.qa_by_name is None

    async def test_empty_list_is_a_noop(self, db: AsyncSession, seed):
        await record_service.attach_task_attribution(db, [])  # must not raise
