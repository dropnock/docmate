"""scripts.mark_lot_inspected: a draft lot gets a passed outgoing inspection
through the normal service path and can then be released."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import AQLStatus
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.lot import LotStatus
from app.models.lot_inspection import InspectionItemStatus, LotInspection, LotInspectionItem, LotInspectionStatus
from app.models.record import RecordStatus
from app.services import lot_inspection_service, lot_service
from scripts import mark_lot_inspected as mark
from tests.test_lot_inspections import _items, _make_lot, _marks, _release, _start


async def _mark(db, seed, lot, **kw):
    kw.setdefault("level", None)
    kw.setdefault("note", mark.DEFAULT_NOTE)
    kw.setdefault("restart", False)
    return await mark.mark_inspected(db, lot, inspector=seed["supervisor"], **kw)


async def _attempts(db, lot):
    return [(i.attempt_number, i.status) for i in (await db.execute(
        select(LotInspection).where(LotInspection.lot_id == lot.id).order_by(LotInspection.attempt_number)
    )).scalars()]


async def test_marks_draft_lot_inspected_and_releasable(db: AsyncSession, seed):
    lot, records = await _make_lot(db, seed, num_records=20)
    inspection = await _mark(db, seed, lot, level=AQLStatus.tightened)

    assert inspection.status == LotInspectionStatus.passed
    assert inspection.inspection_level == AQLStatus.tightened and lot.inspection_level == AQLStatus.tightened
    assert inspection.inspected_by == seed["supervisor"].id
    assert inspection.summary_note == mark.DEFAULT_NOTE
    items = await _items(db, inspection)
    assert len(items) == inspection.sample_size > 0
    assert all(i.status == InspectionItemStatus.accepted for i in items)
    assert lot.status == LotStatus.draft  # not released by the script
    for r in records:
        await db.refresh(r)
        assert r.status == RecordStatus.qa_passed  # nothing requeued

    events = (await db.execute(select(AuditLog.action).where(
        AuditLog.entity_type == AuditEntityType.lot, AuditLog.entity_id == lot.id,
    ))).scalars().all()
    assert events == [AuditAction.sampled, AuditAction.status_changed]

    await _release(db, seed, lot)
    assert lot.status == LotStatus.released


async def test_defaults_to_projects_suggested_level(db: AsyncSession, seed):
    seed["aql_config"].current_status = AQLStatus.reduced
    lot, _ = await _make_lot(db, seed, num_records=10)
    inspection = await _mark(db, seed, lot)
    assert inspection.inspection_level == AQLStatus.reduced


async def test_records_without_document_type_are_accepted(db: AsyncSession, seed):
    """Training copies sit in a cabinet with no batch, so their sampled items
    have no document type and no fields to mark."""
    lot, records = await _make_lot(db, seed, num_records=5)
    for r in records:
        r.batch_id = None
    await db.flush()
    inspection = await _mark(db, seed, lot)
    assert inspection.status == LotInspectionStatus.passed


async def test_already_passed_is_left_alone(db: AsyncSession, seed):
    lot, _ = await _make_lot(db, seed, num_records=5)
    first = await _mark(db, seed, lot)
    again = await _mark(db, seed, lot)
    assert again.id == first.id
    assert await _attempts(db, lot) == [(1, LotInspectionStatus.passed)]


async def test_refuses_while_records_are_in_rework(db: AsyncSession, seed):
    lot, records = await _make_lot(db, seed, num_records=5)
    records[2].status = RecordStatus.pending
    await db.flush()
    with pytest.raises(mark.Refused, match="not qa_passed"):
        await _mark(db, seed, lot)
    assert await _attempts(db, lot) == []


async def test_refuses_non_draft_lot(db: AsyncSession, seed):
    lot, _ = await _make_lot(db, seed, num_records=5)
    await _mark(db, seed, lot)
    await _release(db, seed, lot)
    with pytest.raises(mark.Refused, match="only a draft lot"):
        await _mark(db, seed, lot)


async def test_finishes_an_attempt_in_progress(db: AsyncSession, seed):
    lot, _ = await _make_lot(db, seed, num_records=20)
    started = await _start(db, seed, lot)
    first = (await _items(db, started))[0]
    await lot_inspection_service.review_item(
        db, lot_id=lot.id, inspection_id=started.id, record_id=first.record_id,
        field_results=_marks(), tenant_id=seed["tenant"].id,
    )
    inspection = await _mark(db, seed, lot)
    assert inspection.id == started.id and inspection.status == LotInspectionStatus.passed


async def test_in_progress_attempt_with_a_defect_needs_restart(db: AsyncSession, seed):
    lot, records = await _make_lot(db, seed, num_records=20)
    started = await _start(db, seed, lot)
    first = (await _items(db, started))[0]
    await lot_inspection_service.review_item(
        db, lot_id=lot.id, inspection_id=started.id, record_id=first.record_id,
        field_results=_marks({"surname": "typo"}), tenant_id=seed["tenant"].id,
    )
    with pytest.raises(mark.Refused, match="--restart"):
        await _mark(db, seed, lot)
    assert await _attempts(db, lot) == [(1, LotInspectionStatus.in_progress)]

    inspection = await _mark(db, seed, lot, restart=True)
    assert await _attempts(db, lot) == [(1, LotInspectionStatus.cancelled), (2, LotInspectionStatus.passed)]
    assert inspection.attempt_number == 2
    for r in records:
        await db.refresh(r)
        assert r.status == RecordStatus.qa_passed
    await lot_service.release_lot(db, lot_id=lot.id, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id)


async def test_inspector_must_be_a_digitizing_supervisor(db: AsyncSession, seed):
    lot, _ = await _make_lot(db, seed, num_records=5)
    with pytest.raises(mark.Refused, match="digitizing supervisor"):
        await mark._find_inspector(db, lot, seed["qc_agent"].email)
    assert (await mark._find_inspector(db, lot, seed["supervisor"].email)).id == seed["supervisor"].id
    assert (await mark._find_inspector(db, lot, str(seed["admin"].id))).id == seed["admin"].id
