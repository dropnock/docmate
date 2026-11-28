"""Customer QC on a document type with lists of grouped fields (a caveat
card's caveators, each with a name and an address from #/definitions): every
caveator's name and address is a field of its own to mark (review_fields)."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.batch import Batch
from app.models.document_type import DocumentType
from app.models.lot import LotRecord, LotStatus
from app.models.qc_field_result import QcFieldResult
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskType
from app.schemas.task import FieldResultIn
from app.services import lot_service, review_fields, task_service
from tests.conftest import submit_qc_batches, token
from tests import test_defect_classes

SCHEMA = {
    "$schema": "https://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "caveat_number": {"type": "string", "x-critical": True},
        "caveators": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "address": {"$ref": "#/definitions/address"}},
            },
        },
    },
    "definitions": {"address": {"type": "object", "properties": {"city": {"type": "string"}}}},
}
DATA = {
    "caveat_number": "1257102",
    "caveators": [
        {"name": "Alvin Moncrieffe", "address": {"city": "Kingston"}},
        {"name": "Maureen Moncrieffe", "address": {"city": "Kingston"}},
    ],
}
KEYS = ["caveat_number", "caveators/0/name", "caveators/0/address", "caveators/1/name", "caveators/1/address"]


def marks(defective: dict[str, str] | None = None) -> list[FieldResultIn]:
    defective = defective or {}
    return [
        FieldResultIn(field_key=k, status="defective", error_type=defective[k]) if k in defective
        else FieldResultIn(field_key=k, status="accepted")
        for k in KEYS
    ]


async def _caveat_lot(db, seed):
    """A sampled lot whose records are caveat cards, QC batch created."""
    lot, _ = await test_defect_classes.TestCustomerQc()._sample(db, seed)
    sampled = sorted((await db.execute(
        select(LotRecord.record_id).where(LotRecord.lot_id == lot.id, LotRecord.is_sampled == True)  # noqa: E712
    )).scalars().all())
    doc_type = (await db.execute(
        select(DocumentType).join(Batch, Batch.document_type_id == DocumentType.id)
        .join(Record, Record.batch_id == Batch.id).where(Record.id == sampled[0])
    )).scalar_one()
    doc_type.json_schema = SCHEMA
    for r in (await db.execute(select(Record).where(Record.id.in_(sampled)))).scalars():
        r.indexed_data = DATA
    await lot_service.create_qc_batches(
        db, lot_id=lot.id, project_id=lot.project_id, document_type_id=doc_type.id,
        assignments=[{"agent_id": seed["qc_agent"].id, "record_ids": sampled}],
        supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )
    tasks = (await db.execute(
        select(Task).where(Task.task_type == TaskType.qc).order_by(Task.record_id)
    )).scalars().all()
    return lot, list(tasks)


def _kw(seed, task):
    return dict(task_id=task.id, user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id)


async def _review_all(db, seed, tasks, defects: dict[int, dict[str, str]]):
    for i, t in enumerate(tasks):
        await task_service.start_task(db, **_kw(seed, t))
        if i in defects:
            await task_service.fail_task(db, reason="defect", field_results=marks(defects[i]), **_kw(seed, t))
        else:
            await task_service.complete_task(db, field_results=marks(), **_kw(seed, t))


def test_fields_follow_the_records_entries():
    assert [f["key"] for f in review_fields.review_fields(SCHEMA, DATA)] == KEYS
    assert review_fields.label_for(SCHEMA, "caveators/1/address") == "Caveators 2 › Address"


class TestQc:
    async def test_whole_list_mark_is_refused(self, db: AsyncSession, seed):
        """The old one-mark-per-list shape no longer covers the record."""
        lot, tasks = await _caveat_lot(db, seed)
        await task_service.start_task(db, **_kw(seed, tasks[0]))
        old = [FieldResultIn(field_key="caveat_number", status="accepted"),
               FieldResultIn(field_key="caveators", status="accepted")]
        with pytest.raises(HTTPException) as e:
            await task_service.complete_task(db, field_results=old, **_kw(seed, tasks[0]))
        assert e.value.status_code == 422
        assert "caveators/1/address" in e.value.detail and "unexpected ['caveators']" in e.value.detail

    async def test_each_field_once(self, db: AsyncSession, seed):
        lot, tasks = await _caveat_lot(db, seed)
        await task_service.start_task(db, **_kw(seed, tasks[0]))
        with pytest.raises(HTTPException) as e:
            await task_service.complete_task(
                db, field_results=marks() + [FieldResultIn(field_key="caveat_number", status="accepted")],
                **_kw(seed, tasks[0]),
            )
        assert e.value.status_code == 422

    async def test_second_caveators_address_is_a_defect_of_its_own(self, db: AsyncSession, seed):
        lot, tasks = await _caveat_lot(db, seed)
        await _review_all(db, seed, tasks, {0: {"caveators/1/address": "typo"}, 3: {"caveators/0/address": "typo"}})
        stored = (await db.execute(
            select(QcFieldResult).where(QcFieldResult.task_id == tasks[0].id)
        )).scalars().all()
        assert sorted(m.field_key for m in stored) == sorted(KEYS)
        assert (await db.get(Record, tasks[0].record_id)).status == RecordStatus.qc_failed

        tab = await lot_service.get_field_result_tabulation(db, lot_id=lot.id, tenant_id=seed["tenant"].id)
        rows = {f["field_key"]: f for f in tab["fields"]}
        assert rows["caveators/*/address"]["title"] == "Caveators › Address"
        assert rows["caveators/*/address"]["defective_count"] == 2
        assert rows["caveators/*/address"]["accepted_count"] == 13 * 2 - 2
        assert rows["caveat_number"]["is_critical"] is True

        lot = await submit_qc_batches(db, seed, lot)
        assert lot.status == LotStatus.awaiting_decision
        assert lot.minor_defect_count == 2

    async def test_supervisor_rules_on_one_entry_through_the_api(self, client, db: AsyncSession, seed):
        from tests.test_aql_router import _make_customer_supervisor
        lot, tasks = await _caveat_lot(db, seed)
        await _review_all(db, seed, tasks, {0: {"caveators/1/address": "typo"}})
        await submit_qc_batches(db, seed, lot)
        sup = await _make_customer_supervisor(db, seed)
        await db.commit()
        h = {"Authorization": f"Bearer {token(sup)}", "X-Portal": "customer"}

        review = (await client.get(f"/api/lots/{lot.id}/qc-review", headers=h)).json()
        rec = next(r for r in review["records"] if r["record_id"] == tasks[0].record_id)
        field = next(f for f in rec["fields"] if f["field_key"] == "caveators/1/address")
        assert field["label"] == "Caveators 2 › Address"
        assert field["effective"]["status"] == "defective"

        r = await client.put(
            f"/api/lots/{lot.id}/qc-review/{tasks[0].record_id}/fields/caveators%2F1%2Faddress",
            json={"status": "accepted", "note": "Matches the card"}, headers=h,
        )
        assert r.status_code == 200, r.text
        assert (await db.get(Record, tasks[0].record_id)).status == RecordStatus.qc_passed
