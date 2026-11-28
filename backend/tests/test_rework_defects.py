"""The indexer and QA agent fixing a record see what it was sent back for
(rework_defects.open_rework_defects, GET /api/records/{id}/rework-defects)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.record import Record
from app.models.task import Task, TaskStatus, TaskType
from app.services import lot_service, rework_defects, task_service
from tests import test_lot_inspections as insp
from tests import test_review_fields_qc as caveat
from tests.conftest import decide, token


async def _qa_passes(db, seed, record_id, *, after: datetime):
    """The reworked record's QA check completes."""
    db.add(Task(
        record_id=record_id, batch_id=seed["batch"].id, task_type=TaskType.qa, assigned_to=seed["qa_staff"].id,
        status=TaskStatus.completed, completed_at=after + timedelta(seconds=1),
    ))
    await db.flush()


async def _inspected(db, seed):
    """An outgoing inspection that sends the first sampled record back."""
    lot, _ = await insp._make_lot(db, seed, num_records=20)
    inspection = await insp._start(db, seed, lot, aql_level=2.5)
    await insp._review_all(db, seed, lot, inspection, {0: {"surname": ("Spelt Smyth", "typo")}})
    await insp._complete(db, seed, lot, inspection)
    first = (await insp._items(db, inspection))[0]
    return inspection, await db.get(Record, first.record_id)


class TestInspection:
    async def test_inspection_marks_until_qa_passes(self, db: AsyncSession, seed):
        inspection, record = await _inspected(db, seed)
        open_ = await rework_defects.open_rework_defects(db, record)
        assert open_["source"] == "inspection"
        assert open_["title"].startswith("Outgoing inspection #1 of lot")
        assert open_["note"] is None
        assert open_["fields"] == [{
            "field_key": "surname", "label": "Surname", "error_type": "typo",
            "error_label": "Typographic error", "severity": "minor", "note": "Spelt Smyth",
        }]

        await _qa_passes(db, seed, record.id, after=inspection.completed_at)
        assert await rework_defects.open_rework_defects(db, record) is None

    async def test_a_later_qa_rejection_replaces_it(self, db: AsyncSession, seed):
        inspection, record = await _inspected(db, seed)
        from app.services import audit_service
        from app.models.audit_log import AuditAction, AuditEntityType
        await audit_service.write_event(
            db, tenant_id=seed["tenant"].id, entity_type=AuditEntityType.record, entity_id=record.id,
            action=AuditAction.qa_failed, performed_by=seed["qa_staff"].id, metadata={"reason": "Still misspelt"},
        )
        open_ = await rework_defects.open_rework_defects(db, record)
        assert (open_["source"], open_["note"], open_["fields"]) == ("qa", "Still misspelt", [])

    async def test_never_sent_back(self, db: AsyncSession, seed):
        lot, records = await insp._make_lot(db, seed, num_records=3)
        assert await rework_defects.open_rework_defects(db, records[0]) is None


class TestCustomerQc:
    async def test_only_after_the_decision_and_as_the_supervisor_left_it(self, db: AsyncSession, seed):
        lot, tasks = await caveat._caveat_lot(db, seed)
        await caveat._review_all(db, seed, tasks, {0: {"caveators/1/address": "typo", "caveators/0/name": "typo"}})
        record = await db.get(Record, tasks[0].record_id)
        # Reviewed, not decided: nothing has gone back yet.
        await caveat.submit_qc_batches(db, seed, lot)
        assert await rework_defects.open_rework_defects(db, record) is None

        await lot_service.override_qc_mark(
            db, lot_id=lot.id, record_id=record.id, field_key="caveators/0/name", status="accepted",
            error_type=None, note="Name matches the card", user_id=seed["supervisor"].id,
            tenant_id=seed["tenant"].id,
        )
        await lot_service.override_qc_mark(
            db, lot_id=lot.id, record_id=record.id, field_key="caveators/1/address", status="defective",
            error_type="missing", note="Street left out", user_id=seed["supervisor"].id,
            tenant_id=seed["tenant"].id,
        )
        await lot_service.decide_lot(db, lot_id=lot.id, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id)
        await db.flush()

        open_ = await rework_defects.open_rework_defects(db, record)
        assert open_["source"] == "customer_qc"
        assert [(f["label"], f["error_type"], f["note"]) for f in open_["fields"]] == [
            ("Caveators 2 › Address", "missing", "Street left out"),
        ]
        await db.refresh(lot)
        await _qa_passes(db, seed, record.id, after=lot.decided_at)
        assert await rework_defects.open_rework_defects(db, record) is None

    async def test_dismissed_record_has_nothing_open(self, db: AsyncSession, seed):
        lot, tasks = await caveat._caveat_lot(db, seed)
        await caveat._review_all(db, seed, tasks, {})
        await decide(db, seed, lot)
        assert await rework_defects.open_rework_defects(db, await db.get(Record, tasks[0].record_id)) is None


class TestApi:
    async def test_digitizing_portal_only(self, client, db: AsyncSession, seed):
        inspection, record = await _inspected(db, seed)
        await db.commit()
        de = {"Authorization": f"Bearer {token(seed['indexer'])}", "X-Portal": "digitizing"}
        r = await client.get(f"/api/records/{record.id}/rework-defects", headers=de)
        assert r.status_code == 200, r.text
        assert r.json()["fields"][0]["label"] == "Surname"

        cust = {"Authorization": f"Bearer {token(seed['qc_agent'])}", "X-Portal": "customer"}
        assert (await client.get(f"/api/records/{record.id}/rework-defects", headers=cust)).status_code == 403

    async def test_nothing_open_is_null(self, client, db: AsyncSession, seed):
        lot, records = await insp._make_lot(db, seed, num_records=2)
        await db.commit()
        de = {"Authorization": f"Bearer {token(seed['indexer'])}", "X-Portal": "digitizing"}
        r = await client.get(f"/api/records/{records[0].id}/rework-defects", headers=de)
        assert r.status_code == 200 and r.json() is None
