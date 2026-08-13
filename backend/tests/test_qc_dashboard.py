"""Customer QC reporting: Lot.aql_status_snapshot/qc_completed_at/
critical_defect_count/minor_defect_count, analytics_service.qc_project_summary,
and its endpoint's role gating."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import AQLStatus, SamplingMode
from app.models.batch import Batch, BatchStatus
from app.models.document_type import DocumentType
from app.models.lot import Lot, LotRecord, LotStatus
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.models.user import Portal, User, UserRole
from app.schemas.task import FieldResultIn
from app.services import analytics_service, lot_service, task_service
from tests.conftest import token

SCHEMA = {"type": "object", "properties": {"surname": {"type": "string"}, "dob": {"type": "string", "x-critical": True}}}


async def _make_released_lot(db: AsyncSession, seed, *, num_records=5):
    lot = Lot(
        tenant_id=seed["tenant"].id, project_id=seed["project"].id,
        name="Sample Lot", status=LotStatus.released,
    )
    db.add(lot)
    await db.flush()
    records = [Record(status=RecordStatus.qa_passed, current_version=1) for _ in range(num_records)]
    db.add_all(records)
    await db.flush()
    db.add_all(LotRecord(lot_id=lot.id, record_id=r.id, is_sampled=False) for r in records)
    await db.flush()
    return lot, records


class TestApplySampleAqlStatusSnapshot:
    async def test_iso_mode_snapshots_current_status(self, db: AsyncSession, seed):
        seed["aql_config"].sampling_mode = SamplingMode.iso
        seed["aql_config"].current_status = AQLStatus.tightened
        await db.flush()
        lot, _ = await _make_released_lot(db, seed)

        result = await lot_service.apply_sample(
            db, lot_id=lot.id, sample_rate=None,
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert result.aql_status_snapshot == AQLStatus.tightened

    async def test_manual_mode_snapshots_current_status(self, db: AsyncSession, seed):
        seed["aql_config"].sampling_mode = SamplingMode.manual
        seed["aql_config"].current_status = AQLStatus.reduced
        await db.flush()
        lot, _ = await _make_released_lot(db, seed)

        result = await lot_service.apply_sample(
            db, lot_id=lot.id, sample_rate=0.5,
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert result.aql_status_snapshot == AQLStatus.reduced


async def _make_qc_setup(db: AsyncSession, seed, *, sampling_mode=SamplingMode.iso, acceptance_number=1):
    seed["aql_config"].sampling_mode = sampling_mode
    await db.flush()

    doc_type = DocumentType(project_id=seed["project"].id, name="QC Form", json_schema=SCHEMA)
    db.add(doc_type)
    await db.flush()
    batch = Batch(project_id=seed["project"].id, document_type_id=doc_type.id, name="QC Batch", status=BatchStatus.indexing)
    db.add(batch)
    await db.flush()
    record = Record(batch_id=batch.id, status=RecordStatus.qc_pending, current_version=1)
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


class TestCalculateAccuracyQcSummaryFields:
    async def test_iso_mode_sets_completed_at_and_defect_counts(self, db: AsyncSession, seed):
        task, record, lot = await _make_qc_setup(db, seed, acceptance_number=5)
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
        assert lot.qc_completed_at is not None
        assert lot.critical_defect_count == 0
        assert lot.minor_defect_count == 1

    async def test_manual_mode_sets_completed_at_only(self, db: AsyncSession, seed):
        seed["aql_config"].sampling_mode = SamplingMode.manual
        await db.flush()
        lot = Lot(
            tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Manual Lot",
            status=LotStatus.qc_in_progress, acceptance_number=None,
        )
        db.add(lot)
        record = Record(status=RecordStatus.qc_passed, current_version=1)
        db.add(record)
        await db.flush()
        db.add(LotRecord(lot_id=lot.id, record_id=record.id, is_sampled=True))
        await db.flush()

        result = await lot_service.calculate_accuracy(db, lot_id=lot.id, tenant_id=seed["tenant"].id)
        assert result.qc_completed_at is not None
        assert result.critical_defect_count is None
        assert result.minor_defect_count is None


class TestQcProjectSummary:
    async def test_counts_across_multiple_lots(self, db: AsyncSession, seed):
        # Lot 1: in progress, one record still qc_pending (not yet resolved)
        lot1 = Lot(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="L1", status=LotStatus.qc_in_progress)
        db.add(lot1)
        r1 = Record(status=RecordStatus.qc_pending, current_version=1)
        db.add(r1)
        await db.flush()
        db.add(LotRecord(lot_id=lot1.id, record_id=r1.id, is_sampled=True))

        # Lot 2: passed, one qc_passed sampled record, qc_completed_at set
        lot2 = Lot(
            tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="L2",
            status=LotStatus.passed, qc_completed_at=datetime.now(timezone.utc),
        )
        db.add(lot2)
        r2 = Record(status=RecordStatus.qc_passed, current_version=1)
        db.add(r2)
        await db.flush()
        db.add(LotRecord(lot_id=lot2.id, record_id=r2.id, is_sampled=True))

        # Lot 3: failed, qc_completed_at set
        lot3 = Lot(
            tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="L3",
            status=LotStatus.failed, qc_completed_at=datetime.now(timezone.utc),
        )
        db.add(lot3)
        r3 = Record(status=RecordStatus.qc_failed, current_version=1)
        db.add(r3)
        await db.flush()
        db.add(LotRecord(lot_id=lot3.id, record_id=r3.id, is_sampled=True))

        await db.commit()

        summary = await analytics_service.qc_project_summary(db, project_id=seed["project"].id)
        assert summary["lots_quality_checked"] == 2  # lot2 + lot3 (qc_completed_at set)
        assert summary["lots_rejected"] == 1  # lot3 (failed)
        assert summary["records_passed"] == 1  # r2 only


async def _make_customer_supervisor(db: AsyncSession, seed) -> User:
    supervisor = User(
        tenant_id=seed["tenant"].id, organization_id=seed["cust_org"].id, email="qcdash-sup@test.com",
        keycloak_sub="sub-qcdash-sup", full_name="Customer Supervisor",
        role=UserRole.customer_supervisor, portal=Portal.customer, is_active=True,
    )
    db.add(supervisor)
    await db.commit()
    return supervisor


class TestQcSummaryEndpointRoleGating:
    async def test_customer_supervisor_allowed(self, db: AsyncSession, seed, client):
        supervisor = await _make_customer_supervisor(db, seed)
        sup_token = token(supervisor, portal_override="customer")
        resp = await client.get(
            f"/api/analytics/project-kpis/{seed['project'].id}/qc-summary",
            headers={"Authorization": f"Bearer {sup_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 200
        assert resp.json()["project_id"] == seed["project"].id

    async def test_de_supervisor_allowed(self, db: AsyncSession, seed, client):
        sup_token = token(seed["supervisor"])
        resp = await client.get(
            f"/api/analytics/project-kpis/{seed['project'].id}/qc-summary",
            headers={"Authorization": f"Bearer {sup_token}", "X-Portal": "digitizing"},
        )
        assert resp.status_code == 200

    async def test_qc_agent_forbidden(self, db: AsyncSession, seed, client):
        qc_token = token(seed["qc_agent"], portal_override="customer")
        resp = await client.get(
            f"/api/analytics/project-kpis/{seed['project'].id}/qc-summary",
            headers={"Authorization": f"Bearer {qc_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 403
