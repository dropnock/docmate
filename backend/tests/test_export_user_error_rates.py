"""Per-user raw error rate CSV script (scripts/export_user_error_rates.py).

Tests exercise the internal async query function directly against the test
DB fixture, the same way test_export_records_report.py does — main() opens
its own session via app.core.database.AsyncSessionLocal, which points at
whatever the app is configured against, not the per-test SQLite DB."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus, TaskType
from scripts import export_user_error_rates


def _task(seed, *, task_type: TaskType, status: TaskStatus, assigned_to: int) -> Task:
    return Task(
        record_id=seed["record"].id, batch_id=seed["batch"].id,
        task_type=task_type, assigned_to=assigned_to,
        assigned_by=seed["supervisor"].id, status=status,
    )


class TestExportUserErrorRates:
    async def test_computes_raw_counts_and_rate_per_user_and_task_type(
        self, db: AsyncSession, seed
    ):
        indexer = seed["indexer"].id
        db.add_all([
            _task(seed, task_type=TaskType.indexing, status=TaskStatus.completed, assigned_to=indexer),
            _task(seed, task_type=TaskType.indexing, status=TaskStatus.completed, assigned_to=indexer),
            _task(seed, task_type=TaskType.indexing, status=TaskStatus.failed, assigned_to=indexer),
        ])
        db.add(_task(
            seed, task_type=TaskType.qa, status=TaskStatus.completed, assigned_to=seed["qa_staff"].id
        ))
        await db.flush()

        rows = await export_user_error_rates._fetch_rows(db, tenant_id=None, project_id=None)
        by_key = {(r["user_id"], r["task_type"]): r for r in rows}

        indexer_row = by_key[(indexer, "indexing")]
        assert indexer_row["completed_count"] == 2
        assert indexer_row["failed_count"] == 1
        assert indexer_row["total_attempted"] == 3
        assert indexer_row["error_rate"] == round(1 / 3, 4)
        assert indexer_row["full_name"] == seed["indexer"].full_name
        assert indexer_row["email"] == seed["indexer"].email

        qa_row = by_key[(seed["qa_staff"].id, "qa")]
        assert qa_row["completed_count"] == 1
        assert qa_row["failed_count"] == 0
        assert qa_row["error_rate"] == 0.0

    async def test_ignores_pending_and_in_progress_tasks(self, db: AsyncSession, seed):
        indexer = seed["indexer"].id
        db.add_all([
            _task(seed, task_type=TaskType.indexing, status=TaskStatus.pending, assigned_to=indexer),
            _task(seed, task_type=TaskType.indexing, status=TaskStatus.in_progress, assigned_to=indexer),
        ])
        await db.flush()

        rows = await export_user_error_rates._fetch_rows(db, tenant_id=None, project_id=None)
        assert rows == []

    async def test_project_id_filter(self, db: AsyncSession, seed):
        db.add(_task(
            seed, task_type=TaskType.indexing, status=TaskStatus.completed,
            assigned_to=seed["indexer"].id,
        ))
        await db.flush()

        rows = await export_user_error_rates._fetch_rows(
            db, tenant_id=None, project_id=seed["project"].id + 999
        )
        assert rows == []

        rows = await export_user_error_rates._fetch_rows(
            db, tenant_id=None, project_id=seed["project"].id
        )
        assert len(rows) == 1

    async def test_tenant_id_filter(self, db: AsyncSession, seed):
        db.add(_task(
            seed, task_type=TaskType.indexing, status=TaskStatus.completed,
            assigned_to=seed["indexer"].id,
        ))
        await db.flush()

        rows = await export_user_error_rates._fetch_rows(
            db, tenant_id=seed["tenant"].id + 999, project_id=None
        )
        assert rows == []

        rows = await export_user_error_rates._fetch_rows(
            db, tenant_id=seed["tenant"].id, project_id=None
        )
        assert len(rows) == 1
