"""Resubmitting a remediated lot: the failed customer QC round is archived,
the lot goes back to draft for a fresh outgoing inspection and a fresh
customer sample, and the new round's verdict and tally ignore the old
round's marks."""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import SamplingMode
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.batch import Batch
from app.models.document_type import DocumentType
from app.models.lot import LotQcRound, LotRecord, LotStatus
from app.models.lot_inspection import LotInspection, LotInspectionStatus
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.schemas.task import FieldResultIn
from app.services import analytics_service, lot_service, task_service
from tests.conftest import token
from tests.test_lot_inspections import (
    DE_HEADERS, _complete, _make_customer_supervisor, _make_lot, _release, _review_all, _start,
)

ACCEPT = [FieldResultIn(field_key="surname", status="accepted"), FieldResultIn(field_key="dob", status="accepted")]
CRITICAL = [FieldResultIn(field_key="surname", status="accepted"), FieldResultIn(field_key="dob", status="defective")]


async def _inspect_and_release(db, seed, lot):
    inspection = await _start(db, seed, lot)
    await _review_all(db, seed, lot, inspection)
    await _complete(db, seed, lot, inspection)
    await _release(db, seed, lot)
    return inspection


async def _customer_qc(db, seed, lot, *, fail_first: bool):
    """Sample the whole lot, hand it to the QC agent and review every
    sampled record — failing the first one on a critical field if asked."""
    seed["aql_config"].sampling_mode = SamplingMode.manual
    await lot_service.apply_sample(
        db, lot_id=lot.id, sample_rate=1.0, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )
    # Manual mode doesn't store an acceptance number; set one so the lot is
    # judged by the ISO rule, which reads per-field marks (QcFieldResult).
    lot.acceptance_number = 0
    seed["aql_config"].sampling_mode = SamplingMode.iso
    # The document type the lot's records were indexed under (each
    # _make_lot creates its own).
    doc_type = (await db.execute(
        select(DocumentType).join(Batch, Batch.document_type_id == DocumentType.id)
        .join(Record, Record.batch_id == Batch.id)
        .join(LotRecord, LotRecord.record_id == Record.id)
        .where(LotRecord.lot_id == lot.id).limit(1)
    )).scalar_one()
    sampled = sorted((await db.execute(
        select(LotRecord.record_id).where(LotRecord.lot_id == lot.id, LotRecord.is_sampled == True)  # noqa: E712
    )).scalars().all())
    await lot_service.create_qc_batches(
        db, lot_id=lot.id, project_id=lot.project_id, document_type_id=doc_type.id,
        assignments=[{"agent_id": seed["qc_agent"].id, "record_ids": sampled}],
        supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )
    await db.flush()
    tasks = (await db.execute(
        select(Task).where(Task.record_id.in_(sampled), Task.task_type == TaskType.qc, Task.status == TaskStatus.pending)
        .order_by(Task.record_id)
    )).scalars().all()
    for i, task in enumerate(tasks):
        task.status = TaskStatus.in_progress
        task.started_at = datetime.now(timezone.utc)
        await db.flush()
        if fail_first and i == 0:
            await task_service.fail_task(
                db, task_id=task.id, user_id=seed["qc_agent"].id, reason="dob wrong",
                tenant_id=seed["tenant"].id, field_results=CRITICAL,
            )
        else:
            await task_service.complete_task(
                db, task_id=task.id, user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id,
                field_results=ACCEPT,
            )
    await db.flush()
    await db.refresh(lot)
    return sampled


async def _failed_and_remediated(db, seed):
    lot, records = await _make_lot(db, seed, num_records=3)
    inspection = await _inspect_and_release(db, seed, lot)
    sampled = await _customer_qc(db, seed, lot, fail_first=True)
    assert lot.status == LotStatus.failed
    await lot_service.send_for_remediation(
        db, lot_id=lot.id, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )
    return lot, records, inspection, sampled


async def _finish_rework(db, records):
    """Stand-in for the QA rework pass the rejected record goes through."""
    for r in records:
        await db.refresh(r)
        if r.status == RecordStatus.qc_failed:
            r.status = RecordStatus.qa_passed
    await db.flush()


async def _resubmit(db, seed, lot):
    return await lot_service.resubmit_lot(
        db, lot_id=lot.id, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )


class TestResubmit:
    async def test_blocked_while_records_in_rework(self, db: AsyncSession, seed):
        lot, _, _, _ = await _failed_and_remediated(db, seed)
        with pytest.raises(HTTPException) as exc:
            await _resubmit(db, seed, lot)
        assert exc.value.status_code == 409
        assert "still being reworked" in exc.value.detail

    async def test_only_from_remediation(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=2)
        with pytest.raises(HTTPException) as exc:
            await _resubmit(db, seed, lot)
        assert exc.value.status_code == 409

    async def test_archives_round_and_resets_lot(self, db: AsyncSession, seed):
        lot, records, inspection, sampled = await _failed_and_remediated(db, seed)
        first = dict(
            sample_size=lot.sample_size, accuracy_rate=lot.accuracy_rate,
            critical=lot.critical_defect_count, released_at=lot.released_at,
            qc_completed_at=lot.qc_completed_at,
        )
        await _finish_rework(db, records)
        await _resubmit(db, seed, lot)
        await db.flush()

        assert lot.status == LotStatus.draft and lot.qc_round == 2
        assert lot.sample_size is None and lot.accuracy_rate is None and lot.released_at is None
        assert lot.critical_defect_count is None and lot.qc_completed_at is None

        (archived,) = (await db.execute(select(LotQcRound).where(LotQcRound.lot_id == lot.id))).scalars().all()
        assert archived.round_number == 1 and archived.status == LotStatus.failed
        assert archived.sample_size == first["sample_size"]
        assert archived.accuracy_rate == first["accuracy_rate"]
        assert archived.critical_defect_count == first["critical"] == 1
        assert archived.released_at == first["released_at"]
        assert archived.qc_completed_at == first["qc_completed_at"]
        assert archived.sampled_record_ids == sampled
        assert archived.record_count == 3

        # Every record is back to qa_passed and unsampled.
        for r in records:
            await db.refresh(r)
            assert r.status == RecordStatus.qa_passed
        flags = (await db.execute(select(LotRecord.is_sampled).where(LotRecord.lot_id == lot.id))).scalars().all()
        assert not any(flags)

        await db.refresh(inspection)
        assert inspection.status == LotInspectionStatus.superseded

        # Records the customer had passed get a record-level audit event.
        resets = (await db.execute(select(AuditLog).where(
            AuditLog.entity_type == AuditEntityType.record,
            AuditLog.action == AuditAction.status_changed,
            AuditLog.new_value["status"].as_string() == "qa_passed",
        ))).scalars().all()
        assert sorted(e.entity_id for e in resets) == sampled[1:]
        event = (await db.execute(select(AuditLog).where(AuditLog.action == AuditAction.resubmitted))).scalar_one()
        assert event.entity_type == AuditEntityType.lot
        assert event.new_value == {"status": "draft", "qc_round": 2}
        assert event.metadata_["archived_round_id"] == archived.id

    async def test_second_round_ignores_first_rounds_marks(self, db: AsyncSession, seed):
        lot, records, _, _ = await _failed_and_remediated(db, seed)
        await _finish_rework(db, records)
        await _resubmit(db, seed, lot)

        # Release needs a fresh outgoing inspection.
        with pytest.raises(HTTPException):
            await _release(db, seed, lot)
        await _inspect_and_release(db, seed, lot)

        # Round 2: every sampled record accepted. Round 1's critical defect
        # must not fail the lot or show up in the tally.
        sampled = await _customer_qc(db, seed, lot, fail_first=False)
        assert lot.status == LotStatus.passed
        assert lot.critical_defect_count == 0
        tab = await lot_service.get_field_result_tabulation(db, lot_id=lot.id, tenant_id=seed["tenant"].id)
        by_field = {f["field_key"]: f for f in tab["fields"]}
        assert set(by_field) == {"surname", "dob"}
        assert by_field["dob"]["defective_count"] == 0
        assert by_field["dob"]["accepted_count"] == len(sampled)

    async def test_qc_summary_counts_rounds(self, db: AsyncSession, seed):
        lot, records, _, _ = await _failed_and_remediated(db, seed)
        await _finish_rework(db, records)
        await _resubmit(db, seed, lot)
        await _inspect_and_release(db, seed, lot)
        await _customer_qc(db, seed, lot, fail_first=False)
        summary = await analytics_service.qc_project_summary(db, project_id=seed["project"].id)
        assert summary["lots_quality_checked"] == 2
        assert summary["lots_rejected"] == 1


class TestResubmitApi:
    async def test_supervisor_resubmits_and_customer_sees_rounds(self, db: AsyncSession, seed, client):
        lot, records, _, _ = await _failed_and_remediated(db, seed)
        await _finish_rework(db, records)
        cust = await _make_customer_supervisor(db, seed["cust_org"].id, seed)
        await db.commit()
        cust_h = {"Authorization": f"Bearer {token(cust)}", "X-Portal": "customer"}
        sup_h = {"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": DE_HEADERS}

        assert (await client.post(f"/api/lots/{lot.id}/resubmit", headers=cust_h)).status_code == 403
        resp = await client.post(f"/api/lots/{lot.id}/resubmit", headers=sup_h)
        assert resp.status_code == 200
        assert resp.json()["status"] == "draft" and resp.json()["qc_round"] == 2

        detail = (await client.get(f"/api/lots/{lot.id}", headers=sup_h)).json()
        assert [r["round_number"] for r in detail["qc_rounds"]] == [1]
        assert detail["qc_rounds"][0]["status"] == "failed"

        await _inspect_and_release(db, seed, lot)
        await db.commit()
        detail = (await client.get(f"/api/lots/{lot.id}", headers=cust_h)).json()
        assert detail["qc_round"] == 2 and len(detail["qc_rounds"]) == 1
