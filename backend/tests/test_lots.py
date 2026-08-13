"""Eligibility/empty-batch guards for lot_service.create_qc_batches, plus
apply_sample's ISO 2859-1 / manual sampling_mode branches."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import SamplingMode
from app.models.lot import Lot, LotRecord, LotStatus
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.services import lot_service


async def _make_lot_with_record(db, seed, *, record_status=RecordStatus.qc_pending):
    lot = Lot(
        tenant_id=seed["tenant"].id, project_id=seed["project"].id,
        name="QC Lot", status=LotStatus.qc_in_progress,
    )
    db.add(lot)
    record = Record(status=record_status, current_version=1)
    db.add(record)
    await db.flush()
    db.add(LotRecord(lot_id=lot.id, record_id=record.id, is_sampled=True))
    await db.flush()
    return lot, record


class TestCreateQcBatches:
    async def test_rejects_empty_assignments(self, db: AsyncSession, seed):
        lot, _ = await _make_lot_with_record(db, seed)
        with pytest.raises(HTTPException) as exc_info:
            await lot_service.create_qc_batches(
                db, lot_id=lot.id, project_id=seed["project"].id,
                document_type_id=seed["doc_type"].id, assignments=[],
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_rejects_empty_record_ids_in_assignment(self, db: AsyncSession, seed):
        lot, record = await _make_lot_with_record(db, seed)
        with pytest.raises(HTTPException) as exc_info:
            await lot_service.create_qc_batches(
                db, lot_id=lot.id, project_id=seed["project"].id,
                document_type_id=seed["doc_type"].id,
                assignments=[{"agent_id": seed["qc_agent"].id, "record_ids": []}],
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

        from sqlalchemy import select
        from app.models import Batch
        result = await db.execute(select(Batch).where(Batch.project_id == seed["project"].id, Batch.batch_type == "qc"))
        assert result.scalars().first() is None

    async def test_rejects_record_not_in_lot(self, db: AsyncSession, seed):
        lot, _ = await _make_lot_with_record(db, seed)
        outsider = Record(status=RecordStatus.qc_pending, current_version=1)
        db.add(outsider)
        await db.flush()

        with pytest.raises(HTTPException) as exc_info:
            await lot_service.create_qc_batches(
                db, lot_id=lot.id, project_id=seed["project"].id,
                document_type_id=seed["doc_type"].id,
                assignments=[{"agent_id": seed["qc_agent"].id, "record_ids": [outsider.id]}],
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_rejects_record_already_assigned_to_active_qc_task(self, db: AsyncSession, seed):
        lot, record = await _make_lot_with_record(db, seed)
        # Simulate a prior QC batch already having claimed this record.
        db.add(Task(
            record_id=record.id, batch_id=seed["batch"].id, task_type=TaskType.qc,
            assigned_to=seed["qc_agent"].id, assigned_by=seed["supervisor"].id,
            status=TaskStatus.pending,
        ))
        await db.flush()

        with pytest.raises(HTTPException) as exc_info:
            await lot_service.create_qc_batches(
                db, lot_id=lot.id, project_id=seed["project"].id,
                document_type_id=seed["doc_type"].id,
                assignments=[{"agent_id": seed["qc_agent"].id, "record_ids": [record.id]}],
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_rejects_duplicate_record_across_assignments(self, db: AsyncSession, seed):
        lot, record = await _make_lot_with_record(db, seed)
        with pytest.raises(HTTPException) as exc_info:
            await lot_service.create_qc_batches(
                db, lot_id=lot.id, project_id=seed["project"].id,
                document_type_id=seed["doc_type"].id,
                assignments=[
                    {"agent_id": seed["qc_agent"].id, "record_ids": [record.id]},
                    {"agent_id": seed["qc_agent"].id, "record_ids": [record.id]},
                ],
                supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 400

    async def test_accepts_eligible_record(self, db: AsyncSession, seed):
        lot, record = await _make_lot_with_record(db, seed)
        batches = await lot_service.create_qc_batches(
            db, lot_id=lot.id, project_id=seed["project"].id,
            document_type_id=seed["doc_type"].id,
            assignments=[{"agent_id": seed["qc_agent"].id, "record_ids": [record.id]}],
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert len(batches) == 1

        from sqlalchemy import select
        result = await db.execute(select(Task).where(Task.batch_id == batches[0].id))
        tasks = result.scalars().all()
        assert len(tasks) == 1
        assert tasks[0].record_id == record.id


async def _make_released_lot(db, seed, *, num_records=5):
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


class TestApplySampleIsoMode:
    """seed's AQLConfig defaults to sampling_mode=iso."""

    async def test_computes_sample_size_via_iso_table(self, db: AsyncSession, seed):
        lot, _ = await _make_released_lot(db, seed, num_records=5)
        result = await lot_service.apply_sample(
            db, lot_id=lot.id, sample_rate=None,
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert result.status == LotStatus.qc_in_progress
        # 5 items -> code letter A (2-8) -> AQL 1.5 -> sample 2, accept 0 (see test_aql_service.py)
        assert result.sample_size == 2
        assert result.acceptance_number == 0

        lot_records = (await db.execute(select(LotRecord).where(LotRecord.lot_id == lot.id))).scalars().all()
        assert sum(1 for lr in lot_records if lr.is_sampled) == 2

    async def test_rejects_client_supplied_sample_rate(self, db: AsyncSession, seed):
        lot, _ = await _make_released_lot(db, seed)
        with pytest.raises(HTTPException) as exc_info:
            await lot_service.apply_sample(
                db, lot_id=lot.id, sample_rate=0.5,
                user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 422


class TestApplySampleManualMode:
    async def test_uses_supplied_rate_no_acceptance_number(self, db: AsyncSession, seed):
        seed["aql_config"].sampling_mode = SamplingMode.manual
        await db.flush()
        lot, _ = await _make_released_lot(db, seed, num_records=10)

        result = await lot_service.apply_sample(
            db, lot_id=lot.id, sample_rate=0.5,
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert result.sample_size == 5
        assert result.sample_rate == 0.5
        assert result.acceptance_number is None

    async def test_requires_sample_rate(self, db: AsyncSession, seed):
        seed["aql_config"].sampling_mode = SamplingMode.manual
        await db.flush()
        lot, _ = await _make_released_lot(db, seed)
        with pytest.raises(HTTPException) as exc_info:
            await lot_service.apply_sample(
                db, lot_id=lot.id, sample_rate=None,
                user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 422

    async def test_rejects_out_of_range_rate(self, db: AsyncSession, seed):
        seed["aql_config"].sampling_mode = SamplingMode.manual
        await db.flush()
        lot, _ = await _make_released_lot(db, seed)
        with pytest.raises(HTTPException) as exc_info:
            await lot_service.apply_sample(
                db, lot_id=lot.id, sample_rate=1.5,
                user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 422


class TestCalculateAccuracyManualModeFallback:
    """acceptance_number is only ever set by apply_sample's iso branch — a
    manually-sampled lot (acceptance_number stays None) must keep using the
    flat 90% rule untouched by the new ISO critical/acceptance-number
    branch. See test_qc_field_results.py for the ISO-mode branch coverage."""

    async def test_all_passed_is_accepted(self, db: AsyncSession, seed):
        lot, _ = await _make_lot_with_record(db, seed, record_status=RecordStatus.qc_passed)
        result = await lot_service.calculate_accuracy(db, lot_id=lot.id, tenant_id=seed["tenant"].id)
        assert result.accuracy_rate == 1.0
        assert result.status == LotStatus.passed

    async def test_all_failed_is_rejected(self, db: AsyncSession, seed):
        lot, _ = await _make_lot_with_record(db, seed, record_status=RecordStatus.qc_failed)
        result = await lot_service.calculate_accuracy(db, lot_id=lot.id, tenant_id=seed["tenant"].id)
        assert result.accuracy_rate == 0.0
        assert result.status == LotStatus.failed
