"""CSV records report script (scripts/export_records_report.py).

Tests exercise the internal async generator directly against the test DB
fixture, the same way test_backfill_tiff_to_pdf.py does — main() opens its
own session via app.core.database.AsyncSessionLocal, which points at
whatever the app is configured against, not the per-test SQLite DB."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet import Cabinet
from app.models.record import Record, RecordStatus
from scripts import export_records_report


async def _collect(db: AsyncSession, **kwargs) -> list[dict]:
    return [row async for row in export_records_report._stream_rows(db, **kwargs)]


class TestExportRecordsReport:
    async def test_resolves_project_via_batch_when_no_cabinet(self, db: AsyncSession, seed):
        # seed's record/record2 are batch-linked with no cabinet_id — project
        # must fall back to batch.project_id.
        rows = await _collect(db, tenant_id=None, project_id=None, statuses=None)
        by_id = {r["record_id"]: r for r in rows}
        assert by_id[seed["record"].id]["project_id"] == seed["project"].id
        assert by_id[seed["record"].id]["project_name"] == seed["project"].name
        assert by_id[seed["record"].id]["tenant_id"] == seed["tenant"].id
        assert by_id[seed["record"].id]["batch_name"] == seed["batch"].name
        assert by_id[seed["record"].id]["cabinet_name"] is None

    async def test_resolves_project_via_cabinet_for_unassigned_record(self, db: AsyncSession, seed):
        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Intake Cabinet")
        db.add(cabinet)
        await db.flush()

        unassigned = Record(cabinet_id=cabinet.id, status=RecordStatus.pending, current_version=1)
        db.add(unassigned)
        await db.flush()

        rows = await _collect(db, tenant_id=None, project_id=None, statuses=None)
        row = next(r for r in rows if r["record_id"] == unassigned.id)
        assert row["project_id"] == seed["project"].id
        assert row["cabinet_name"] == "Intake Cabinet"
        assert row["batch_name"] is None

    async def test_includes_locker_email_when_locked(self, db: AsyncSession, seed):
        from datetime import datetime, timezone
        seed["record"].locked_by = seed["indexer"].id
        seed["record"].locked_at = datetime.now(timezone.utc)
        await db.flush()

        rows = await _collect(db, tenant_id=None, project_id=None, statuses=None)
        row = next(r for r in rows if r["record_id"] == seed["record"].id)
        assert row["locked_by_email"] == seed["indexer"].email

        other = next(r for r in rows if r["record_id"] == seed["record2"].id)
        assert other["locked_by_email"] is None

    async def test_status_filter(self, db: AsyncSession, seed):
        seed["record"].status = RecordStatus.qa_failed
        seed["record2"].status = RecordStatus.indexed
        await db.flush()

        rows = await _collect(
            db, tenant_id=None, project_id=None, statuses=[RecordStatus.qa_failed]
        )
        assert {r["record_id"] for r in rows} == {seed["record"].id}
        assert rows[0]["status"] == "qa_failed"

    async def test_project_id_filter(self, db: AsyncSession, seed):
        rows = await _collect(
            db, tenant_id=None, project_id=seed["project"].id + 999, statuses=None
        )
        assert rows == []

        rows = await _collect(
            db, tenant_id=None, project_id=seed["project"].id, statuses=None
        )
        assert {r["record_id"] for r in rows} == {seed["record"].id, seed["record2"].id}

    async def test_tenant_id_filter(self, db: AsyncSession, seed):
        rows = await _collect(
            db, tenant_id=seed["tenant"].id + 999, project_id=None, statuses=None
        )
        assert rows == []

        rows = await _collect(
            db, tenant_id=seed["tenant"].id, project_id=None, statuses=None
        )
        assert {r["record_id"] for r in rows} == {seed["record"].id, seed["record2"].id}
