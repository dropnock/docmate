"""AQLs on the project create / edit screens: stored on the project's
AQLConfig, audited on the project, and never applied to an inspection or
customer sample that had already started."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import AQLConfig
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.services import lot_inspection_service
from tests.conftest import token
from tests.test_defect_classes import TestCustomerQc
from tests.test_lot_inspections import _start, _make_lot


def _h(user):
    return {"Authorization": f"Bearer {token(user)}", "X-Portal": "digitizing"}


async def _events(db, project_id):
    return (await db.execute(
        select(AuditLog).where(
            AuditLog.entity_type == AuditEntityType.project, AuditLog.entity_id == project_id,
        ).order_by(AuditLog.id)
    )).scalars().all()


class TestCreate:
    async def test_create_with_aqls(self, client, seed, db: AsyncSession):
        resp = await client.post("/api/projects", headers=_h(seed["admin"]), json={
            "customer_org_id": seed["cust_org"].id, "name": "New",
            "critical_aql": 0.65, "major_aql": 1.0, "minor_aql": 2.5,
        })
        assert resp.status_code == 201, resp.text
        pid = resp.json()["id"]
        cfg = (await db.execute(select(AQLConfig).where(AQLConfig.project_id == pid))).scalar_one()
        assert (cfg.critical_aql, cfg.normal_aql, cfg.minor_aql) == (0.65, 1.0, 2.5)
        [event] = await _events(db, pid)
        assert event.action == AuditAction.created
        assert event.performed_by == seed["admin"].id
        assert event.new_value["name"] == "New"
        assert (event.new_value["critical_aql"], event.new_value["major_aql"], event.new_value["minor_aql"]) == (0.65, 1.0, 2.5)

    async def test_defaults(self, client, seed, db: AsyncSession):
        resp = await client.post("/api/projects", headers=_h(seed["admin"]), json={
            "customer_org_id": seed["cust_org"].id, "name": "Defaults",
        })
        cfg = (await db.execute(select(AQLConfig).where(AQLConfig.project_id == resp.json()["id"]))).scalar_one()
        assert (cfg.critical_aql, cfg.normal_aql, cfg.minor_aql) == (None, 1.5, 4.0)

    async def test_rejects_an_aql_off_the_table(self, client, seed, db: AsyncSession):
        resp = await client.post("/api/projects", headers=_h(seed["admin"]), json={
            "customer_org_id": seed["cust_org"].id, "name": "Bad", "minor_aql": 3.0,
        })
        assert resp.status_code == 422 and "minor_aql" in resp.text


class TestEdit:
    async def test_edit_aqls_and_details(self, client, seed, db: AsyncSession):
        pid = seed["project"].id
        resp = await client.patch(f"/api/projects/{pid}", headers=_h(seed["supervisor"]), json={
            "description": "Caveat cards", "critical_aql": 0.65, "major_aql": 1.0, "minor_aql": 2.5,
        })
        assert resp.status_code == 200, resp.text
        cfg = seed["aql_config"]
        await db.flush()
        await db.refresh(cfg)
        assert (cfg.critical_aql, cfg.normal_aql, cfg.minor_aql) == (0.65, 1.0, 2.5)
        events = await _events(db, pid)
        assert [e.action for e in events] == [AuditAction.settings_changed, AuditAction.settings_changed]
        details, aqls = events
        assert details.new_value == {"description": "Caveat cards"}
        assert details.old_value == {"description": None}
        assert aqls.old_value == {"critical_aql": None, "normal_aql": 1.5, "minor_aql": 1.5}
        assert aqls.new_value == {"critical_aql": 0.65, "normal_aql": 1.0, "minor_aql": 2.5}
        assert all(e.performed_by == seed["supervisor"].id for e in events)

    async def test_back_to_zero_tolerance(self, client, seed, db: AsyncSession):
        cfg = seed["aql_config"]
        cfg.critical_aql = 0.65
        await db.commit()
        resp = await client.patch(f"/api/projects/{seed['project'].id}", headers=_h(seed["supervisor"]),
                                  json={"critical_aql": None})
        assert resp.status_code == 200
        await db.flush()
        await db.refresh(cfg)
        assert cfg.critical_aql is None

    async def test_unchanged_values_write_no_event(self, client, seed, db: AsyncSession):
        p = seed["project"]
        await client.patch(f"/api/projects/{p.id}", headers=_h(seed["supervisor"]),
                           json={"name": p.name, "major_aql": 1.5})
        assert await _events(db, p.id) == []

    async def test_major_cant_be_zero_tolerance(self, client, seed):
        resp = await client.patch(f"/api/projects/{seed['project'].id}", headers=_h(seed["supervisor"]),
                                  json={"major_aql": None})
        assert resp.status_code == 422

    async def test_history(self, client, seed, db: AsyncSession):
        pid = seed["project"].id
        await client.patch(f"/api/projects/{pid}", headers=_h(seed["supervisor"]), json={"minor_aql": 4.0})
        await client.patch(f"/api/projects/{pid}", headers=_h(seed["supervisor"]), json={"name": "Renamed"})
        resp = await client.get(f"/api/projects/{pid}/history", headers=_h(seed["supervisor"]))
        assert resp.status_code == 200
        events = resp.json()
        assert [e["new_value"] for e in events] == [{"name": "Renamed"}, {"minor_aql": 4.0}]  # newest first
        assert events[0]["actor_name"] == seed["supervisor"].full_name
        assert (await client.get(f"/api/projects/{pid}/history", headers=_h(seed["indexer"]))).status_code == 403


class TestEarlierWorkKeepsItsAqls:
    async def test_outgoing_inspection(self, client, seed, db: AsyncSession):
        from tests.test_lot_inspections import _complete, _review_all
        cfg = seed["aql_config"]
        cfg.critical_aql, cfg.normal_aql, cfg.minor_aql = None, 1.0, 4.0
        await db.flush()
        lot, _ = await _make_lot(db, seed, num_records=20)
        inspection = await _start(db, seed, lot)  # n=13, minor Ac 1
        await db.commit()
        # Tighten minor mid-inspection: 0.65 would make minor Ac 0.
        resp = await client.patch(f"/api/projects/{seed['project'].id}", headers=_h(seed["supervisor"]),
                                  json={"minor_aql": 0.65})
        assert resp.status_code == 200
        await _review_all(db, seed, lot, inspection, {0: {"surname": ("", "typo")}})
        await _complete(db, seed, lot, inspection)
        assert inspection.status.value == "passed"  # judged against minor 4.0, Ac 1
        report = await lot_inspection_service.build_report(db, lot=lot)
        assert report["inspections"][0]["classes"]["minor"]["aql"] == 4.0
        assert report["inspections"][0]["classes"]["minor"]["acceptance_number"] == 1

    async def test_customer_sample(self, client, seed, db: AsyncSession):
        qc = TestCustomerQc()
        lot, _ = await qc._sample(db, seed)  # minor 4.0, Ac 1
        await db.commit()
        resp = await client.patch(f"/api/projects/{seed['project'].id}", headers=_h(seed["supervisor"]),
                                  json={"minor_aql": 0.65})
        assert resp.status_code == 200
        await qc._review(db, seed, lot, {0: qc._bad("surname", "typo")})
        assert lot.status.value == "passed"
        assert lot.class_plan["minor"]["aql"] == 4.0
