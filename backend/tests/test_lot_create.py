"""create_lot's guards: records must be qa_passed, belong to the lot's
project, and not already be in a lot."""
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.models.tenant import Tenant
from app.models.organization import Organization, OrgType
from app.services import lot_service
from tests.conftest import token
from tests.test_lot_inspections import DE_HEADERS, _make_lot


async def _records(db, seed, n=2, **kw):
    rows = [Record(batch_id=seed["batch"].id, status=RecordStatus.qa_passed, current_version=1, **kw) for _ in range(n)]
    db.add_all(rows)
    await db.flush()
    return rows


async def _create(db, seed, ids, project_id=None):
    return await lot_service.create_lot(
        db, project_id=project_id or seed["project"].id, name="L", description=None,
        record_ids=ids, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )


class TestOneLotPerRecord:
    async def test_record_already_in_a_lot_rejected(self, db: AsyncSession, seed):
        a, b = await _records(db, seed)
        first = await _create(db, seed, [a.id])
        with pytest.raises(HTTPException) as exc:
            await _create(db, seed, [b.id, a.id])
        assert exc.value.status_code == 409
        assert f"{a.id} (lot {first.id})" in exc.value.detail

    async def test_removed_record_can_join_another_lot(self, db: AsyncSession, seed):
        lot, records = await _make_lot(db, seed, num_records=2)
        await lot_service.remove_lot_records(
            db, lot_id=lot.id, record_ids=[records[0].id],
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        new = await lot_service.create_lot(
            db, project_id=lot.project_id, name="Second", description=None, record_ids=[records[0].id],
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert new.id != lot.id

    async def test_repeated_ids_in_one_request_are_collapsed(self, db: AsyncSession, seed):
        (a,) = await _records(db, seed, n=1)
        lot = await _create(db, seed, [a.id, a.id])
        await db.flush()
        await db.refresh(lot, ["lot_records"])
        assert [lr.record_id for lr in lot.lot_records] == [a.id]


class TestProjectBoundary:
    async def test_record_from_another_project_rejected(self, db: AsyncSession, seed):
        from app.models.batch import Batch, BatchStatus
        other = Project(
            tenant_id=seed["tenant"].id, digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id, name="Other",
        )
        db.add(other)
        await db.flush()
        batch = Batch(project_id=other.id, document_type_id=seed["doc_type"].id, name="B", status=BatchStatus.passed)
        db.add(batch)
        await db.flush()
        stray = Record(batch_id=batch.id, status=RecordStatus.qa_passed, current_version=1)
        db.add(stray)
        await db.flush()
        with pytest.raises(HTTPException) as exc:
            await _create(db, seed, [stray.id])
        assert exc.value.status_code == 422

    async def test_project_in_another_tenant_rejected_over_api(self, db: AsyncSession, seed, client):
        t2 = Tenant(name="Other Tenant", slug="other")
        db.add(t2)
        await db.flush()
        o1 = Organization(tenant_id=t2.id, name="DE2", type=OrgType.digitizing_entity)
        o2 = Organization(tenant_id=t2.id, name="C2", type=OrgType.customer)
        db.add_all([o1, o2])
        await db.flush()
        foreign = Project(tenant_id=t2.id, digitizing_org_id=o1.id, customer_org_id=o2.id, name="Foreign")
        db.add(foreign)
        await db.commit()
        resp = await client.post(
            "/api/lots",
            json={"project_id": foreign.id, "name": "x", "record_ids": [seed["record"].id]},
            headers={"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": DE_HEADERS},
        )
        assert resp.status_code == 404

    async def test_empty_record_list_rejected_over_api(self, db: AsyncSession, seed, client):
        await db.commit()
        resp = await client.post(
            "/api/lots", json={"project_id": seed["project"].id, "name": "x", "record_ids": []},
            headers={"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": DE_HEADERS},
        )
        assert resp.status_code == 422


class TestPickerFilter:
    async def test_cabinet_records_not_in_lot(self, db: AsyncSession, seed, client):
        lot, records = await _make_lot(db, seed, num_records=2)
        free = Record(cabinet_id=records[0].cabinet_id, status=RecordStatus.qa_passed, current_version=1)
        db.add(free)
        await db.commit()
        h = {"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": DE_HEADERS}
        url = f"/api/cabinets/{records[0].cabinet_id}/records?status=qa_passed"
        assert len((await client.get(url, headers=h)).json()) == 3
        only_free = (await client.get(url + "&not_in_lot=true", headers=h)).json()
        assert [r["id"] for r in only_free] == [free.id]
