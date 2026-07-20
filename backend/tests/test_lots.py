"""Eligibility/empty-batch guards for lot_service.create_qc_batches."""
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

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
