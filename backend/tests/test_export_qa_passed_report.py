"""QA-passed report script (scripts/export_qa_passed_report.py).

Exercises the query helpers against the test DB fixture — main() opens its
own session via app.core.database.AsyncSessionLocal, which points at
whatever the app is configured against, not the per-test SQLite DB."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.record import RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from scripts import export_qa_passed_report as report

CARD = {"caveat_number": "1254611", "registered_titles": [{"volume_number": "1235", "folio_number": "717"}]}


def _task(seed, record, task_type, user, status, minutes):
    return Task(
        record_id=record.id, batch_id=seed["batch"].id, task_type=task_type, assigned_to=user.id,
        status=status, completed_at=datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes),
    )


class TestQaPassedReport:
    async def test_only_qa_passed_records_of_the_project(self, db: AsyncSession, seed):
        seed["record"].status = RecordStatus.qa_passed
        seed["record2"].status = RecordStatus.indexed
        await db.flush()

        assert [r.id for r in await report._records(db, seed["project"].id)] == [seed["record"].id]
        assert await report._records(db, seed["project"].id + 999) == []

    async def test_credits_the_indexer_and_qa_of_the_passing_version(self, db: AsyncSession, seed):
        record = seed["record"]
        record.status = RecordStatus.qa_passed
        record.indexed_data = CARD
        db.add_all([
            # First pass indexed by indexer, failed at QA, re-indexed by indexer2, then passed.
            _task(seed, record, TaskType.indexing, seed["indexer"], TaskStatus.failed, 0),
            _task(seed, record, TaskType.qa, seed["qa_staff"], TaskStatus.failed, 10),
            _task(seed, record, TaskType.indexing, seed["indexer2"], TaskStatus.completed, 20),
            _task(seed, record, TaskType.qa, seed["supervisor"], TaskStatus.completed, 30),
        ])
        await db.flush()

        people = await report._latest_completers(db, [record.id])
        assert people[(record.id, TaskType.indexing)] == seed["indexer2"].full_name
        assert people[(record.id, TaskType.qa)] == seed["supervisor"].full_name
        assert report.file_stem(record.indexed_data) == ("1254611_1235_717", None)

    async def test_no_completed_tasks_credits_nobody(self, db: AsyncSession, seed):
        assert await report._latest_completers(db, [seed["record2"].id]) == {}
