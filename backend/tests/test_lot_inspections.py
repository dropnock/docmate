"""Digitizing supervisor's outgoing ISO 2859-1 inspection of a draft lot —
lot_inspection_service, the release gate in lot_service.release_lot, and
the customer-facing inspection report endpoint."""
import random

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditEntityType, AuditLog
from app.models.batch import Batch, BatchStatus
from app.models.cabinet import Cabinet
from app.models.document_type import DocumentType
from app.models.lot import Lot, LotRecord, LotStatus
from app.models.lot_inspection import InspectionItemStatus, LotInspectionItem, LotInspectionStatus
from app.models.organization import Organization, OrgType
from app.models.record import Record, RecordStatus
from app.models.user import Portal, User, UserRole
from app.schemas.task import FieldResultIn
from app.services import aql_service, lot_inspection_service, lot_service
from tests.conftest import token

SCHEMA = {
    "type": "object",
    "properties": {
        "surname": {"type": "string", "title": "Surname"},
        "dob": {"type": "string", "title": "Date of Birth", "x-critical": True},
    },
}

DE_HEADERS = "digitizing"


def _marks(defective: dict[str, str | None] | None = None) -> list[FieldResultIn]:
    defective = defective or {}
    return [
        FieldResultIn(
            field_key=k,
            status="defective" if k in defective else "accepted",
            note=defective.get(k),
        )
        for k in SCHEMA["properties"]
    ]


async def _make_lot(db: AsyncSession, seed, *, num_records: int) -> tuple[Lot, list[Record]]:
    """Draft lot of qa_passed records, in a cabinet (so rework can requeue
    them to indexing) under a document type with one critical field."""
    doc_type = DocumentType(project_id=seed["project"].id, name="Inspect Form", json_schema=SCHEMA)
    cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Cab")
    db.add_all([doc_type, cabinet])
    await db.flush()
    batch = Batch(
        project_id=seed["project"].id, cabinet_id=cabinet.id, document_type_id=doc_type.id,
        name="Indexed", status=BatchStatus.passed,
    )
    db.add(batch)
    await db.flush()
    records = [
        Record(
            cabinet_id=cabinet.id, batch_id=batch.id, status=RecordStatus.qa_passed,
            current_version=1, source_identifier=f"SRC-{i}", indexed_data={"surname": f"S{i}", "dob": "2000-01-01"},
        )
        for i in range(num_records)
    ]
    db.add_all(records)
    await db.flush()
    lot = Lot(
        tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Outgoing Lot",
        status=LotStatus.draft,
    )
    db.add(lot)
    await db.flush()
    db.add_all([LotRecord(lot_id=lot.id, record_id=r.id) for r in records])
    await db.flush()
    return lot, records


async def _start(db, seed, lot, aql_level=1.5):
    return await lot_inspection_service.start_inspection(
        db, lot_id=lot.id, aql_level=aql_level,
        user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )


async def _items(db, inspection) -> list[LotInspectionItem]:
    return list((await db.execute(
        select(LotInspectionItem).where(LotInspectionItem.inspection_id == inspection.id)
        .order_by(LotInspectionItem.record_id)
    )).scalars().all())


async def _review_all(db, seed, lot, inspection, defects_by_index: dict[int, dict] | None = None):
    defects_by_index = defects_by_index or {}
    for i, item in enumerate(await _items(db, inspection)):
        await lot_inspection_service.review_item(
            db, lot_id=lot.id, inspection_id=inspection.id, record_id=item.record_id,
            field_results=_marks(defects_by_index.get(i)), tenant_id=seed["tenant"].id,
        )


async def _complete(db, seed, lot, inspection):
    return await lot_inspection_service.complete_inspection(
        db, lot_id=lot.id, inspection_id=inspection.id, summary_note=None,
        user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )


async def _release(db, seed, lot):
    return await lot_service.release_lot(
        db, lot_id=lot.id, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )


class TestSamplingPlan:
    def test_code_letter_sample_and_numbers(self):
        # Lot of 20 -> code letter C; at AQL 2.5 the plan is n=5, Ac=1.
        assert aql_service.sampling_plan(20, 2.5) == {
            "code_letter": "C", "sample_size": 5, "acceptance_number": 1, "rejection_number": 2,
        }

    def test_sample_capped_at_lot_size(self):
        assert aql_service.sampling_plan(1, 1.5)["sample_size"] == 1

    def test_rejects_untabulated_aql(self):
        with pytest.raises(HTTPException) as exc:
            aql_service.sampling_plan(20, 2.0)
        assert exc.value.status_code == 422


class TestStartInspection:
    async def test_draws_reproducible_random_sample(self, db: AsyncSession, seed):
        lot, records = await _make_lot(db, seed, num_records=20)
        inspection = await _start(db, seed, lot, aql_level=2.5)

        assert inspection.sample_size == 5
        assert inspection.acceptance_number == 1
        assert inspection.code_letter == "C"
        item_ids = sorted(i.record_id for i in await _items(db, inspection))
        expected = sorted(random.Random(inspection.random_seed).sample(sorted(r.id for r in records), 5))
        assert item_ids == expected

    async def test_requires_all_records_qa_passed(self, db: AsyncSession, seed):
        lot, records = await _make_lot(db, seed, num_records=3)
        records[0].status = RecordStatus.pending
        await db.flush()
        with pytest.raises(HTTPException) as exc:
            await _start(db, seed, lot)
        assert exc.value.status_code == 409

    async def test_only_one_in_progress(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=3)
        await _start(db, seed, lot)
        with pytest.raises(HTTPException) as exc:
            await _start(db, seed, lot)
        assert exc.value.status_code == 409

    async def test_released_lot_cannot_be_inspected(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=3)
        lot.status = LotStatus.released
        await db.flush()
        with pytest.raises(HTTPException) as exc:
            await _start(db, seed, lot)
        assert exc.value.status_code == 409


class TestReviewAndComplete:
    async def test_review_must_cover_every_field(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=3)
        inspection = await _start(db, seed, lot)
        item = (await _items(db, inspection))[0]
        with pytest.raises(HTTPException) as exc:
            await lot_inspection_service.review_item(
                db, lot_id=lot.id, inspection_id=inspection.id, record_id=item.record_id,
                field_results=[FieldResultIn(field_key="surname", status="accepted")],
                tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 422

    async def test_cannot_complete_with_unreviewed_records(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=3)
        inspection = await _start(db, seed, lot)
        with pytest.raises(HTTPException) as exc:
            await _complete(db, seed, lot, inspection)
        assert exc.value.status_code == 409

    async def test_clean_sample_passes_and_lot_releases(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=3)
        inspection = await _start(db, seed, lot)
        await _review_all(db, seed, lot, inspection)
        await _complete(db, seed, lot, inspection)

        assert inspection.status == LotInspectionStatus.passed
        assert (inspection.critical_defect_count, inspection.minor_defect_count) == (0, 0)
        await _release(db, seed, lot)
        assert lot.status == LotStatus.released

    async def test_minor_defects_over_acceptance_number_fail_and_rework(self, db: AsyncSession, seed):
        # Lot of 3 at AQL 1.5 -> code letter A, n=2, Ac=0: one minor defect fails.
        lot, _ = await _make_lot(db, seed, num_records=3)
        inspection = await _start(db, seed, lot)
        await _review_all(db, seed, lot, inspection, {0: {"surname": "misspelt"}})
        await _complete(db, seed, lot, inspection)

        assert inspection.status == LotInspectionStatus.failed
        assert inspection.minor_defect_count == 1
        assert inspection.defective_record_count == 1
        defective = next(i for i in await _items(db, inspection) if i.status == InspectionItemStatus.defective)
        record = await db.get(Record, defective.record_id)
        assert record.status == RecordStatus.pending
        assert record.batch_id is None

        with pytest.raises(HTTPException) as exc:
            await _release(db, seed, lot)
        assert exc.value.status_code == 409
        # Re-inspection waits for the rework to come back through QA.
        with pytest.raises(HTTPException):
            await _start(db, seed, lot)
        record.status = RecordStatus.qa_passed
        await db.flush()
        second = await _start(db, seed, lot)
        assert second.attempt_number == 2

    async def test_passed_with_defects_still_waits_for_rework(self, db: AsyncSession, seed):
        # Lot of 20 at AQL 2.5 -> n=5, Ac=1: one minor defect is accepted.
        lot, _ = await _make_lot(db, seed, num_records=20)
        inspection = await _start(db, seed, lot, aql_level=2.5)
        await _review_all(db, seed, lot, inspection, {2: {"surname": None}})
        await _complete(db, seed, lot, inspection)

        assert inspection.status == LotInspectionStatus.passed
        with pytest.raises(HTTPException) as exc:
            await _release(db, seed, lot)
        assert "rework" in exc.value.detail
        defective = next(i for i in await _items(db, inspection) if i.status == InspectionItemStatus.defective)
        (await db.get(Record, defective.record_id)).status = RecordStatus.qa_passed
        await db.flush()
        await _release(db, seed, lot)
        assert lot.status == LotStatus.released

    async def test_critical_defect_fails_regardless_of_acceptance_number(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=20)
        inspection = await _start(db, seed, lot, aql_level=2.5)
        await _review_all(db, seed, lot, inspection, {0: {"dob": "wrong year"}})
        await _complete(db, seed, lot, inspection)

        assert inspection.acceptance_number == 1
        assert inspection.critical_defect_count == 1
        assert inspection.status == LotInspectionStatus.failed

    async def test_cancelled_attempt_allows_a_fresh_one(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=3)
        first = await _start(db, seed, lot)
        await lot_inspection_service.cancel_inspection(
            db, lot_id=lot.id, inspection_id=first.id,
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        second = await _start(db, seed, lot)
        assert second.attempt_number == 2

    async def test_audit_events_keyed_to_lot(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=3)
        inspection = await _start(db, seed, lot)
        await _review_all(db, seed, lot, inspection)
        await _complete(db, seed, lot, inspection)
        rows = (await db.execute(
            select(AuditLog).where(AuditLog.entity_type == AuditEntityType.lot, AuditLog.entity_id == lot.id)
        )).scalars().all()
        assert len(rows) == 2


class TestReleaseGate:
    async def test_release_without_inspection_rejected(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=3)
        with pytest.raises(HTTPException) as exc:
            await _release(db, seed, lot)
        assert exc.value.status_code == 409


async def _make_customer_supervisor(db: AsyncSession, org_id: int, seed, sub="sub-cust-sup") -> User:
    user = User(
        tenant_id=seed["tenant"].id, organization_id=org_id, email=f"{sub}@test.com",
        keycloak_sub=sub, full_name="Cust Sup",
        role=UserRole.customer_supervisor, portal=Portal.customer, is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


class TestInspectionReportEndpoint:
    async def _inspected_lot(self, db, seed):
        lot, _ = await _make_lot(db, seed, num_records=3)
        inspection = await _start(db, seed, lot)
        await _review_all(db, seed, lot, inspection)
        await _complete(db, seed, lot, inspection)
        await db.commit()
        return lot

    async def test_customer_sees_report_only_after_release(self, db: AsyncSession, seed, client):
        lot = await self._inspected_lot(db, seed)
        cust = await _make_customer_supervisor(db, seed["cust_org"].id, seed)
        await db.commit()
        headers = {"Authorization": f"Bearer {token(cust)}", "X-Portal": "customer"}

        resp = await client.get(f"/api/lots/{lot.id}/inspection-report", headers=headers)
        assert resp.status_code == 404

        sup_headers = {"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": DE_HEADERS}
        resp = await client.post(f"/api/lots/{lot.id}/release", headers=sup_headers)
        assert resp.status_code == 200

        resp = await client.get(f"/api/lots/{lot.id}/inspection-report", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["final_status"] == "passed"
        assert len(body["inspections"]) == 1
        report = body["inspections"][0]
        assert report["sample_size"] == 2 and report["acceptance_number"] == 0
        assert len(report["items"]) == 2
        assert {f["title"] for f in report["items"][0]["field_results"]} == {"Surname", "Date of Birth"}

    async def test_other_customer_org_cannot_see_report(self, db: AsyncSession, seed, client):
        lot = await self._inspected_lot(db, seed)
        await _release(db, seed, lot)
        other_org = Organization(tenant_id=seed["tenant"].id, name="Other Cust", type=OrgType.customer)
        db.add(other_org)
        await db.flush()
        outsider = await _make_customer_supervisor(db, other_org.id, seed, sub="sub-outsider")
        await db.commit()

        resp = await client.get(
            f"/api/lots/{lot.id}/inspection-report",
            headers={"Authorization": f"Bearer {token(outsider)}", "X-Portal": "customer"},
        )
        assert resp.status_code == 404

    async def test_supervisor_inspection_flow_over_api(self, db: AsyncSession, seed, client):
        lot, _ = await _make_lot(db, seed, num_records=3)
        await db.commit()
        headers = {"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": DE_HEADERS}

        resp = await client.get(f"/api/lots/{lot.id}/inspections", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["default_aql_level"] == 1.5
        assert resp.json()["available_aql_levels"] == [0.65, 1.0, 1.5, 2.5]

        resp = await client.post(f"/api/lots/{lot.id}/inspections", json={"aql_level": 1.5}, headers=headers)
        assert resp.status_code == 201
        detail = resp.json()
        assert detail["reviewed_count"] == 0
        for item in detail["items"]:
            resp = await client.put(
                f"/api/lots/{lot.id}/inspections/{detail['id']}/items/{item['record_id']}",
                json={"field_results": [{"field_key": k, "status": "accepted"} for k in SCHEMA["properties"]]},
                headers=headers,
            )
            assert resp.status_code == 200
        resp = await client.post(
            f"/api/lots/{lot.id}/inspections/{detail['id']}/complete",
            json={"summary_note": "Clean sample"}, headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "passed"
        assert resp.json()["summary_note"] == "Clean sample"

    async def test_customer_cannot_run_inspections(self, db: AsyncSession, seed, client):
        lot, _ = await _make_lot(db, seed, num_records=3)
        cust = await _make_customer_supervisor(db, seed["cust_org"].id, seed)
        await db.commit()
        resp = await client.post(
            f"/api/lots/{lot.id}/inspections", json={"aql_level": 1.5},
            headers={"Authorization": f"Bearer {token(cust)}", "X-Portal": "customer"},
        )
        assert resp.status_code == 403
