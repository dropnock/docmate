"""Lot and cabinet audit events are filed under their own entity types, not
entity_type=record — so they never surface in the history of a record that
happens to share the id — plus the lot history endpoint and the backfill
that retypes rows written by the old code."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.organization import Organization, OrgType
from app.models.record import Record, RecordStatus
from app.services import audit_service, cabinet_service, lot_service
from scripts import backfill_lot_audit_entity_type as backfill
from tests.conftest import token
from tests.test_lot_inspections import (
    DE_HEADERS, _complete, _make_customer_supervisor, _make_lot, _release, _review_all, _start,
)


async def _passed_and_released(db, seed):
    lot, records = await _make_lot(db, seed, num_records=3)
    inspection = await _start(db, seed, lot)
    await _review_all(db, seed, lot, inspection)
    await _complete(db, seed, lot, inspection)
    await _release(db, seed, lot)
    return lot, records


async def _events(db, entity_type, entity_id):
    return list((await db.execute(
        select(AuditLog).where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
    )).scalars().all())


class TestEntityTypes:
    async def test_lot_events_are_lot_typed(self, db: AsyncSession, seed):
        records = [Record(status=RecordStatus.qa_passed, current_version=1) for _ in range(2)]
        db.add_all(records)
        await db.flush()
        lot = await lot_service.create_lot(
            db, project_id=seed["project"].id, name="L", description=None,
            record_ids=[r.id for r in records], user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()

        assert [e.action for e in await _events(db, AuditEntityType.lot, lot.id)] == [AuditAction.created]
        assert await _events(db, AuditEntityType.record, lot.id) == []

    async def test_release_not_in_history_of_record_sharing_the_id(self, db: AsyncSession, seed):
        lot, records = await _passed_and_released(db, seed)
        await db.flush()
        # Whatever record has the lot's id must not see the lot's events.
        history = await audit_service.get_record_history(db, tenant_id=seed["tenant"].id, record_id=lot.id)
        assert not any((e.new_value or {}).get("status") == "released" for e in history)

        lot_history = await audit_service.get_lot_history(db, tenant_id=seed["tenant"].id, lot_id=lot.id)
        assert any((e.new_value or {}).get("status") == "released" for e in lot_history)

    async def test_cabinet_created_is_cabinet_typed(self, db: AsyncSession, seed):
        cabinet = await cabinet_service.create_cabinet(
            db, project_id=seed["project"].id, organization_id=None, name="C2", description=None,
            user_id=seed["admin"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        assert len(await _events(db, AuditEntityType.cabinet, cabinet.id)) == 1
        assert await _events(db, AuditEntityType.record, cabinet.id) == []


class TestLotHistoryEndpoint:
    async def test_supervisor_sees_full_history(self, db: AsyncSession, seed, client):
        lot, _ = await _passed_and_released(db, seed)
        await db.commit()
        resp = await client.get(
            f"/api/lots/{lot.id}/history",
            headers={"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": DE_HEADERS},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(e["entity_type"] == "lot" and e["entity_id"] == lot.id for e in body)
        # Outgoing-inspection events precede the release for the digitizing side.
        assert body[-1]["new_value"]["status"] == "released"
        assert len(body) > 1

    async def test_customer_sees_history_from_release_onward(self, db: AsyncSession, seed, client):
        lot, _ = await _make_lot(db, seed, num_records=3)
        cust = await _make_customer_supervisor(db, seed["cust_org"].id, seed)
        await db.commit()
        headers = {"Authorization": f"Bearer {token(cust)}", "X-Portal": "customer"}

        resp = await client.get(f"/api/lots/{lot.id}/history", headers=headers)
        assert resp.status_code == 404  # still a draft

        inspection = await _start(db, seed, lot)
        await _review_all(db, seed, lot, inspection)
        await _complete(db, seed, lot, inspection)
        await _release(db, seed, lot)
        await db.commit()

        resp = await client.get(f"/api/lots/{lot.id}/history", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["new_value"] == {"status": "released"}

    async def test_other_customer_org_gets_404(self, db: AsyncSession, seed, client):
        lot, _ = await _passed_and_released(db, seed)
        other_org = Organization(tenant_id=seed["tenant"].id, name="Other Cust", type=OrgType.customer)
        db.add(other_org)
        await db.flush()
        outsider = await _make_customer_supervisor(db, other_org.id, seed, sub="sub-outsider")
        await db.commit()
        resp = await client.get(
            f"/api/lots/{lot.id}/history",
            headers={"Authorization": f"Bearer {token(outsider)}", "X-Portal": "customer"},
        )
        assert resp.status_code == 404


class TestBackfill:
    async def _legacy(self, db, seed, **kw):
        row = AuditLog(
            tenant_id=seed["tenant"].id, entity_type=AuditEntityType.record,
            performed_at=datetime.now(timezone.utc) - timedelta(days=1), **kw,
        )
        db.add(row)
        await db.flush()
        return row

    async def test_retypes_only_old_lot_and_cabinet_shapes(self, db: AsyncSession, seed):
        lot_created = await self._legacy(db, seed, entity_id=7, action=AuditAction.created,
                                         new_value={"name": "L", "record_count": 3})
        lot_released = await self._legacy(db, seed, entity_id=7, action=AuditAction.status_changed,
                                          old_value={"status": "draft"}, new_value={"status": "released"})
        lot_sampled = await self._legacy(db, seed, entity_id=7, action=AuditAction.sampled,
                                         new_value={"sample_rate": 0.2, "sample_size": 1, "total": 3})
        lot_finalised = await self._legacy(db, seed, entity_id=7, action=AuditAction.status_changed,
                                           new_value={"status": "passed", "accuracy_rate": 1.0})
        lot_remediation = await self._legacy(db, seed, entity_id=7, action=AuditAction.status_changed,
                                             old_value={"status": "failed"}, new_value={"status": "remediation"})
        cab_created = await self._legacy(db, seed, entity_id=7, action=AuditAction.created,
                                         new_value={"name": "Cab", "project_id": 1})
        # Genuine record events with the same id — must be left alone.
        rec_created = await self._legacy(db, seed, entity_id=7, action=AuditAction.created,
                                         new_value={"source": "image_ingest", "original_filename": "a.tif"})
        rec_status = await self._legacy(db, seed, entity_id=7, action=AuditAction.status_changed,
                                        old_value={"status": "in_progress"}, new_value={"status": "failed"})

        misfiled = await backfill._find_misfiled(db, tenant_id=None)
        await backfill._apply_fix(db, misfiled)
        await db.flush()

        for row in (lot_created, lot_released, lot_sampled, lot_finalised, lot_remediation):
            assert row.entity_type == AuditEntityType.lot
        assert cab_created.entity_type == AuditEntityType.cabinet
        assert rec_created.entity_type == AuditEntityType.record
        assert rec_status.entity_type == AuditEntityType.record
        assert lot_created.entity_id == 7  # only the type changes

        # Idempotent: a second run finds nothing.
        assert await backfill._find_misfiled(db, tenant_id=None) == []

    async def test_tenant_filter(self, db: AsyncSession, seed):
        await self._legacy(db, seed, entity_id=7, action=AuditAction.sampled, new_value={"total": 1})
        assert await backfill._find_misfiled(db, tenant_id=seed["tenant"].id + 999) == []
        assert len(await backfill._find_misfiled(db, tenant_id=seed["tenant"].id)) == 1
