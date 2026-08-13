"""Per-field QC defect marking (ISO 2859-1) — complete_task/fail_task's
QcFieldResult persistence, lot_service.calculate_accuracy's critical/
acceptance-number branch, and the supervisor tabulation endpoint."""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import SamplingMode
from app.models.batch import Batch, BatchStatus
from app.models.document_type import DocumentType
from app.models.lot import Lot, LotRecord, LotStatus
from app.models.qc_field_result import QcFieldResult, QcFieldStatus
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.schemas.task import FieldResultIn
from app.services import lot_service, task_service
from tests.conftest import token

SCHEMA = {
    "type": "object",
    "properties": {
        "surname": {"type": "string"},
        "dob": {"type": "string", "x-critical": True},
    },
}


async def _make_qc_setup(
    db: AsyncSession, seed, *, sampling_mode=SamplingMode.iso, acceptance_number: int | None = 1,
):
    """One sampled record with a QC task in_progress, assigned to seed's
    qc_agent, under a document type with one critical field (dob) and one
    non-critical field (surname). Returns (task, record, lot)."""
    seed["aql_config"].sampling_mode = sampling_mode
    await db.flush()

    doc_type = DocumentType(project_id=seed["project"].id, name="QC Form", json_schema=SCHEMA)
    db.add(doc_type)
    await db.flush()

    batch = Batch(
        project_id=seed["project"].id, document_type_id=doc_type.id,
        name="QC Batch", status=BatchStatus.indexing,
    )
    db.add(batch)
    await db.flush()

    record = Record(cabinet_id=None, batch_id=batch.id, status=RecordStatus.qc_pending, current_version=1)
    db.add(record)
    await db.flush()

    lot = Lot(
        tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="QC Lot",
        status=LotStatus.qc_in_progress, acceptance_number=acceptance_number,
    )
    db.add(lot)
    await db.flush()
    db.add(LotRecord(lot_id=lot.id, record_id=record.id, is_sampled=True))

    task = Task(
        record_id=record.id, batch_id=batch.id, task_type=TaskType.qc,
        assigned_to=seed["qc_agent"].id, assigned_by=seed["supervisor"].id,
        status=TaskStatus.in_progress, started_at=datetime.now(timezone.utc),
    )
    db.add(task)
    await db.flush()

    return task, record, lot


class TestCompleteTaskIsoMode:
    async def test_all_accepted_persists_rows_and_passes(self, db: AsyncSession, seed):
        task, record, lot = await _make_qc_setup(db, seed)
        await task_service.complete_task(
            db, task_id=task.id, user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id,
            field_results=[
                FieldResultIn(field_key="surname", status="accepted"),
                FieldResultIn(field_key="dob", status="accepted"),
            ],
        )
        await db.commit()

        rows = (await db.execute(select(QcFieldResult).where(QcFieldResult.task_id == task.id))).scalars().all()
        assert len(rows) == 2
        assert {r.field_key: r.status for r in rows} == {"surname": QcFieldStatus.accepted, "dob": QcFieldStatus.accepted}
        dob_row = next(r for r in rows if r.field_key == "dob")
        assert dob_row.is_critical is True

        record = await db.get(Record, record.id)
        assert record.status == RecordStatus.qc_passed

        lot = await db.get(Lot, lot.id)
        assert lot.status == LotStatus.passed

    async def test_rejects_defective_field_via_complete(self, db: AsyncSession, seed):
        task, record, lot = await _make_qc_setup(db, seed)
        with pytest.raises(HTTPException) as exc_info:
            await task_service.complete_task(
                db, task_id=task.id, user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id,
                field_results=[
                    FieldResultIn(field_key="surname", status="defective"),
                    FieldResultIn(field_key="dob", status="accepted"),
                ],
            )
        assert exc_info.value.status_code == 422

    async def test_rejects_incomplete_field_results(self, db: AsyncSession, seed):
        task, record, lot = await _make_qc_setup(db, seed)
        with pytest.raises(HTTPException) as exc_info:
            await task_service.complete_task(
                db, task_id=task.id, user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id,
                field_results=[FieldResultIn(field_key="surname", status="accepted")],
            )
        assert exc_info.value.status_code == 422


class TestFailTaskIsoMode:
    async def test_non_critical_defect_within_acceptance_number_passes_lot(self, db: AsyncSession, seed):
        task, record, lot = await _make_qc_setup(db, seed, acceptance_number=1)
        await task_service.fail_task(
            db, task_id=task.id, user_id=seed["qc_agent"].id, reason="unused",
            tenant_id=seed["tenant"].id,
            field_results=[
                FieldResultIn(field_key="surname", status="defective", note="Misspelled"),
                FieldResultIn(field_key="dob", status="accepted"),
            ],
        )
        await db.commit()

        record = await db.get(Record, record.id)
        assert record.status == RecordStatus.qc_failed  # per-record disposition: any defect -> rework

        lot = await db.get(Lot, lot.id)
        assert lot.status == LotStatus.passed  # 1 non-critical defect <= acceptance_number(1)

    async def test_non_critical_defect_exceeding_acceptance_number_fails_lot(self, db: AsyncSession, seed):
        task, record, lot = await _make_qc_setup(db, seed, acceptance_number=0)
        await task_service.fail_task(
            db, task_id=task.id, user_id=seed["qc_agent"].id, reason="unused",
            tenant_id=seed["tenant"].id,
            field_results=[
                FieldResultIn(field_key="surname", status="defective"),
                FieldResultIn(field_key="dob", status="accepted"),
            ],
        )
        await db.commit()
        lot = await db.get(Lot, lot.id)
        assert lot.status == LotStatus.failed

    async def test_critical_defect_fails_lot_regardless_of_acceptance_number(self, db: AsyncSession, seed):
        task, record, lot = await _make_qc_setup(db, seed, acceptance_number=100)
        await task_service.fail_task(
            db, task_id=task.id, user_id=seed["qc_agent"].id, reason="unused",
            tenant_id=seed["tenant"].id,
            field_results=[
                FieldResultIn(field_key="surname", status="accepted"),
                FieldResultIn(field_key="dob", status="defective", note="Illegible"),
            ],
        )
        await db.commit()
        lot = await db.get(Lot, lot.id)
        assert lot.status == LotStatus.failed  # critical defect -> immediate fail even with a huge acceptance_number

    async def test_rejects_no_defective_field_via_fail(self, db: AsyncSession, seed):
        task, record, lot = await _make_qc_setup(db, seed)
        with pytest.raises(HTTPException) as exc_info:
            await task_service.fail_task(
                db, task_id=task.id, user_id=seed["qc_agent"].id, reason="unused",
                tenant_id=seed["tenant"].id,
                field_results=[
                    FieldResultIn(field_key="surname", status="accepted"),
                    FieldResultIn(field_key="dob", status="accepted"),
                ],
            )
        assert exc_info.value.status_code == 422

    async def test_still_creates_rework_batch(self, db: AsyncSession, seed):
        """The existing rework-routing behavior (see test_qc_rejection_rework.py)
        must be unaffected by field_results validation/persistence."""
        task, record, lot = await _make_qc_setup(db, seed)
        await task_service.fail_task(
            db, task_id=task.id, user_id=seed["qc_agent"].id, reason="unused",
            tenant_id=seed["tenant"].id,
            field_results=[
                FieldResultIn(field_key="surname", status="defective"),
                FieldResultIn(field_key="dob", status="accepted"),
            ],
        )
        await db.flush()
        rework_task = (await db.execute(
            select(Task).where(
                Task.record_id == record.id, Task.task_type == TaskType.qa,
                Task.status == TaskStatus.pending, Task.assigned_to.is_(None),
            )
        )).scalar_one()
        assert rework_task is not None


class TestManualModeUnaffected:
    async def test_field_results_ignored_in_manual_mode(self, db: AsyncSession, seed):
        task, record, lot = await _make_qc_setup(db, seed, sampling_mode=SamplingMode.manual, acceptance_number=None)
        # A manual-mode lot never has acceptance_number set by apply_sample —
        # matches this test's setup (None), same as the real flow.
        await task_service.complete_task(
            db, task_id=task.id, user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id,
            field_results=None,
        )
        await db.commit()

        rows = (await db.execute(select(QcFieldResult).where(QcFieldResult.task_id == task.id))).scalars().all()
        assert rows == []

        record = await db.get(Record, record.id)
        assert record.status == RecordStatus.qc_passed


class TestFieldResultTabulation:
    async def test_aggregates_across_multiple_records_and_agents(self, db: AsyncSession, seed):
        from app.models.user import Portal, User, UserRole

        other_qc_agent = User(
            tenant_id=seed["tenant"].id, organization_id=seed["cust_org"].id, email="qc2@test.com",
            keycloak_sub="sub-qc2", full_name="Second QC Agent",
            role=UserRole.customer_qc_agent, portal=Portal.customer, is_active=True,
        )
        db.add(other_qc_agent)
        await db.flush()

        seed["aql_config"].sampling_mode = SamplingMode.iso
        await db.flush()
        doc_type = DocumentType(project_id=seed["project"].id, name="QC Form 2", json_schema=SCHEMA)
        db.add(doc_type)
        await db.flush()
        batch = Batch(
            project_id=seed["project"].id, document_type_id=doc_type.id,
            name="Tab Batch", status=BatchStatus.indexing,
        )
        db.add(batch)
        await db.flush()
        lot = Lot(
            tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Tab Lot",
            status=LotStatus.qc_in_progress, acceptance_number=5,
        )
        db.add(lot)
        await db.flush()

        for agent, surname_status in [(seed["qc_agent"], "defective"), (other_qc_agent, "accepted")]:
            record = Record(batch_id=batch.id, status=RecordStatus.qc_pending, current_version=1)
            db.add(record)
            await db.flush()
            db.add(LotRecord(lot_id=lot.id, record_id=record.id, is_sampled=True))
            task = Task(
                record_id=record.id, batch_id=batch.id, task_type=TaskType.qc,
                assigned_to=agent.id, assigned_by=seed["supervisor"].id,
                status=TaskStatus.in_progress, started_at=datetime.now(timezone.utc),
            )
            db.add(task)
            await db.flush()
            field_results = [
                FieldResultIn(field_key="surname", status=surname_status),
                FieldResultIn(field_key="dob", status="accepted"),
            ]
            if surname_status == "defective":
                await task_service.fail_task(
                    db, task_id=task.id, user_id=agent.id, reason="unused",
                    tenant_id=seed["tenant"].id, field_results=field_results,
                )
            else:
                await task_service.complete_task(
                    db, task_id=task.id, user_id=agent.id, tenant_id=seed["tenant"].id,
                    field_results=field_results,
                )
        await db.commit()

        tabulation = await lot_service.get_field_result_tabulation(db, lot_id=lot.id, tenant_id=seed["tenant"].id)
        assert tabulation["any_critical_defect"] is False
        assert tabulation["non_critical_defect_total"] == 1

        surname_summary = next(f for f in tabulation["fields"] if f["field_key"] == "surname")
        assert surname_summary["defective_count"] == 1
        assert surname_summary["accepted_count"] == 1
        assert surname_summary["is_critical"] is False
        agent_ids = {a["id"] for a in surname_summary["contributing_agents"]}
        assert agent_ids == {seed["qc_agent"].id, other_qc_agent.id}

        dob_summary = next(f for f in tabulation["fields"] if f["field_key"] == "dob")
        assert dob_summary["accepted_count"] == 2
        assert dob_summary["defective_count"] == 0
        assert dob_summary["is_critical"] is True

    async def test_endpoint_role_gated(self, db: AsyncSession, seed, client):
        _, _, lot = await _make_qc_setup(db, seed)
        await db.commit()

        qc_token = token(seed["qc_agent"], portal_override="customer")
        resp = await client.get(
            f"/api/lots/{lot.id}/qc-field-results",
            headers={"Authorization": f"Bearer {qc_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 403
