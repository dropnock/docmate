"""The customer QC supervisor reviews a lot's defects before it is decided:
the lot waits in awaiting_decision, their changes to reviewers' marks are
what the counts and the ISO verdict use, and rejected records only go back
for rework once they confirm (lot_service.override_qc_mark / decide_lot)."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.lot import LotStatus
from app.models.qc_field_result import QcFieldResult
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskType
from app.services import lot_service
from tests.conftest import decide, submit_qc_batches, token
from tests.test_aql_router import _make_customer_supervisor
from tests.test_defect_classes import TestCustomerQc

# 20-record lot, critical zero tolerance / major 1.0 / minor 4.0:
# n=13, Ac critical 0 / major 0 / minor 1 (see TestCustomerQc).
QC = TestCustomerQc()
bad = TestCustomerQc._bad


async def _reviewed(db, seed, defects):
    lot, records = await QC._sample(db, seed)
    await QC._review(db, seed, lot, defects, decide=False)
    await submit_qc_batches(db, seed, lot)
    sampled = await lot_service._sampled(db, lot.id)
    return lot, sampled


def _kw(seed, lot, record, field, **extra):
    return dict(
        lot_id=lot.id, record_id=record.id, field_key=field,
        user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id, **extra,
    )


async def _qa_tasks(db, record_ids):
    return (await db.execute(
        select(Task).where(Task.record_id.in_(record_ids), Task.task_type == TaskType.qa)
    )).scalars().all()


class TestAwaitingDecision:
    async def test_last_review_waits_for_the_supervisor(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {0: bad("dob", "typo")})
        assert lot.status == LotStatus.awaiting_decision
        assert lot.critical_defect_count == 1  # provisional counts are shown
        assert lot.qc_completed_at is None
        # No rework yet, and the project's switching counters haven't moved.
        assert await _qa_tasks(db, [r.id for r in sampled]) == []
        assert seed["aql_config"].current_status.value == "normal"

    async def test_decide_applies_the_verdict_and_sends_rework(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {0: bad("dob", "typo")})
        await decide(db, seed, lot)
        assert lot.status == LotStatus.failed
        assert lot.decided_by == seed["supervisor"].id and lot.qc_completed_at is not None
        # One failure tightens the project in the test config.
        assert seed["aql_config"].current_status.value == "tightened"
        tasks = await _qa_tasks(db, [r.id for r in sampled])
        assert [t.record_id for t in tasks] == [sampled[0].id]
        event = (await db.execute(select(AuditLog).where(AuditLog.action == AuditAction.qc_lot_decided))).scalar_one()
        assert event.entity_type == AuditEntityType.lot and event.entity_id == lot.id
        assert event.new_value["status"] == "failed"
        assert event.metadata_["records_sent_for_rework"] == [sampled[0].id]

    async def test_decide_twice_is_409(self, db: AsyncSession, seed):
        lot, _ = await _reviewed(db, seed, {})
        await decide(db, seed, lot)
        with pytest.raises(HTTPException) as e:
            await lot_service.decide_lot(db, lot_id=lot.id, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id)
        assert e.value.status_code == 409

    async def test_not_before_every_record_is_reviewed(self, db: AsyncSession, seed):
        lot, _ = await QC._sample(db, seed)
        with pytest.raises(HTTPException) as e:
            await lot_service.decide_lot(db, lot_id=lot.id, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id)
        assert e.value.status_code == 409


class TestOverrides:
    async def test_dismissing_the_only_critical_defect_passes_the_lot(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {0: bad("dob", "typo")})
        result = await lot_service.override_qc_mark(
            db, **_kw(seed, lot, sampled[0], "dob", status="accepted", error_type=None, note="Date is right on the card"),
        )
        assert result["accepted"] is True
        assert sampled[0].status == RecordStatus.qc_passed
        await decide(db, seed, lot)
        assert lot.status == LotStatus.passed and lot.critical_defect_count == 0
        assert await _qa_tasks(db, [r.id for r in sampled]) == []
        # The reviewer's mark is kept beside the override.
        mark = (await db.execute(select(QcFieldResult).where(
            QcFieldResult.record_id == sampled[0].id, QcFieldResult.field_key == "dob",
        ))).scalar_one()
        assert mark.status.value == "defective" and mark.override_status.value == "accepted"
        assert mark.overridden_by == seed["supervisor"].id

    async def test_marking_an_accepted_field_defective_rejects_the_record(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {})
        result = await lot_service.override_qc_mark(
            db, **_kw(seed, lot, sampled[3], "surname", status="defective", error_type="missing", note="Surname left out"),
        )
        assert result["defects_by_class"]["major"] == 1 and result["accepted"] is False
        assert sampled[3].status == RecordStatus.qc_failed
        await decide(db, seed, lot)
        assert lot.status == LotStatus.failed and lot.major_defect_count == 1
        assert [t.record_id for t in await _qa_tasks(db, [r.id for r in sampled])] == [sampled[3].id]

    async def test_changing_the_error_type_changes_the_class(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {0: bad("surname", "missing")})
        assert lot.major_defect_count == 1
        result = await lot_service.override_qc_mark(
            db, **_kw(seed, lot, sampled[0], "surname", status="defective", error_type="typo", note="Only a typo"),
        )
        assert result["defects_by_class"] == {"critical": 0, "major": 0, "minor": 1}
        assert result["accepted"] is True
        assert sampled[0].status == RecordStatus.qc_failed  # still a defect: reworked

    async def test_identifier_field_stays_critical(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {0: bad("dob", "missing")})
        result = await lot_service.override_qc_mark(
            db, **_kw(seed, lot, sampled[0], "dob", status="defective", error_type="typo", note="typo, not missing"),
        )
        assert result["defects_by_class"]["critical"] == 1

    async def test_a_change_needs_a_reason(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {0: bad("dob", "typo")})
        with pytest.raises(HTTPException) as e:
            await lot_service.override_qc_mark(
                db, **_kw(seed, lot, sampled[0], "dob", status="accepted", error_type=None, note="  "),
            )
        assert e.value.status_code == 422

    async def test_defect_needs_an_error_type(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {})
        with pytest.raises(HTTPException) as e:
            await lot_service.override_qc_mark(
                db, **_kw(seed, lot, sampled[0], "surname", status="defective", error_type=None, note="x"),
            )
        assert e.value.status_code == 422

    async def test_upholding_clears_the_override(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {0: bad("dob", "typo")})
        await lot_service.override_qc_mark(
            db, **_kw(seed, lot, sampled[0], "dob", status="accepted", error_type=None, note="fine"),
        )
        # Back to the reviewer's mark: no reason needed, nothing overridden.
        result = await lot_service.override_qc_mark(
            db, **_kw(seed, lot, sampled[0], "dob", status="defective", error_type="typo", note=None),
        )
        assert result["accepted"] is False
        assert sampled[0].status == RecordStatus.qc_failed
        mark = (await db.execute(select(QcFieldResult).where(
            QcFieldResult.record_id == sampled[0].id, QcFieldResult.field_key == "dob",
        ))).scalar_one()
        assert mark.override_status is None and mark.overridden_by is None
        events = (await db.execute(select(AuditLog).where(AuditLog.action == AuditAction.qc_mark_overridden))).scalars().all()
        assert [e.metadata_["reverted_to_reviewer"] for e in events] == [False, True]
        assert all(e.entity_type == AuditEntityType.record and e.entity_id == sampled[0].id for e in events)

    async def test_tabulation_counts_the_overrides(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {0: bad("surname", "missing")})
        await lot_service.override_qc_mark(
            db, **_kw(seed, lot, sampled[0], "surname", status="defective", error_type="typo", note="typo"),
        )
        tab = await lot_service.get_field_result_tabulation(db, lot_id=lot.id, tenant_id=seed["tenant"].id)
        surname = next(f for f in tab["fields"] if f["field_key"] == "surname")
        assert surname["by_class"] == {"critical": 0, "major": 0, "minor": 1}

    async def test_no_changes_after_the_decision(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {0: bad("dob", "typo")})
        await decide(db, seed, lot)
        with pytest.raises(HTTPException) as e:
            await lot_service.override_qc_mark(
                db, **_kw(seed, lot, sampled[0], "dob", status="accepted", error_type=None, note="late"),
            )
        assert e.value.status_code == 409

    async def test_record_outside_the_sample_is_404(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {})
        unsampled = (await db.execute(
            select(Record).where(Record.id.notin_([r.id for r in sampled]), Record.batch_id == sampled[0].batch_id)
        )).scalars().first()
        with pytest.raises(HTTPException) as e:
            await lot_service.override_qc_mark(
                db, **_kw(seed, lot, unsampled, "dob", status="accepted", error_type=None, note=None),
            )
        assert e.value.status_code == 404


class TestReview:
    async def test_lists_reviewer_override_and_effective_marks(self, db: AsyncSession, seed):
        lot, sampled = await _reviewed(db, seed, {0: bad("dob", "typo")})
        await lot_service.override_qc_mark(
            db, **_kw(seed, lot, sampled[0], "dob", status="accepted", error_type=None, note="fine"),
        )
        review = await lot_service.get_qc_review(db, lot_id=lot.id, tenant_id=seed["tenant"].id)
        assert review["status"] == "awaiting_decision" and review["accepted"] is True
        assert review["classes"]["critical"]["found"] == 0
        first = review["records"][0]
        assert first["record_id"] == sampled[0].id and first["reviewer_passed"] is False
        assert first["reviewer_name"] == seed["qc_agent"].full_name
        assert first["overridden"] is True and first["defect_count"] == 0
        dob = next(f for f in first["fields"] if f["field_key"] == "dob")
        assert dob["reviewer"]["status"] == "defective" and dob["reviewer"]["severity"] == "critical"
        assert dob["override"]["status"] == "accepted" and dob["override"]["note"] == "fine"
        assert dob["effective"]["status"] == "accepted"
        assert len(review["records"]) == 13


class TestManualRate:
    async def test_supervisor_rules_on_whole_records(self, db: AsyncSession, seed):
        from tests.test_lot_resubmit import _customer_qc, _inspect_and_release
        from tests.test_lot_inspections import _make_lot
        lot, _ = await _make_lot(db, seed, num_records=3)
        await _inspect_and_release(db, seed, lot)
        sampled = await _customer_qc(db, seed, lot, fail_first=True, decide=False)
        await submit_qc_batches(db, seed, lot)
        lot.acceptance_number = None  # judged as a manual-rate lot
        await db.flush()
        assert lot.status == LotStatus.awaiting_decision
        record = await db.get(Record, sampled[0])
        with pytest.raises(HTTPException) as e:
            await lot_service.override_record_outcome(
                db, lot_id=lot.id, record_id=record.id, passed=True, note=None,
                user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert e.value.status_code == 422
        result = await lot_service.override_record_outcome(
            db, lot_id=lot.id, record_id=record.id, passed=True, note="Reviewer misread the card",
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert result["accuracy_rate"] == 1.0 and result["accepted"] is True
        await decide(db, seed, lot)
        assert lot.status == LotStatus.passed
        assert await _qa_tasks(db, sampled) == []


class TestApi:
    async def _setup(self, db, seed):
        lot, sampled = await _reviewed(db, seed, {0: bad("dob", "typo")})
        sup = await _make_customer_supervisor(db, seed)
        return lot, sampled, {"Authorization": f"Bearer {token(sup, portal_override='customer')}", "X-Portal": "customer"}

    async def test_supervisor_reviews_overrides_and_decides(self, client, db: AsyncSession, seed):
        lot, sampled, h = await self._setup(db, seed)
        r = await client.get(f"/api/lots/{lot.id}/qc-review", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["accepted"] is False
        r = await client.put(
            f"/api/lots/{lot.id}/qc-review/{sampled[0].id}/fields/dob", headers=h,
            json={"status": "accepted", "note": "Date matches the card"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["accepted"] is True
        r = await client.post(f"/api/lots/{lot.id}/decide", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "passed" and r.json()["decided_at"]

    async def test_reviewer_cannot_override_or_decide(self, client, db: AsyncSession, seed):
        lot, sampled, _ = await self._setup(db, seed)
        h = {"Authorization": f"Bearer {token(seed['qc_agent'])}", "X-Portal": "customer"}
        assert (await client.get(f"/api/lots/{lot.id}/qc-review", headers=h)).status_code == 403
        assert (await client.post(f"/api/lots/{lot.id}/decide", headers=h)).status_code == 403
        r = await client.put(
            f"/api/lots/{lot.id}/qc-review/{sampled[0].id}/fields/dob", headers=h, json={"status": "accepted"},
        )
        assert r.status_code == 403

    async def test_digitizing_supervisor_cannot_decide(self, client, db: AsyncSession, seed):
        lot, _, _ = await self._setup(db, seed)
        h = {"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": "digitizing"}
        assert (await client.post(f"/api/lots/{lot.id}/decide", headers=h)).status_code == 403
