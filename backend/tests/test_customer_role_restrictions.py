"""Customer QC agents must not reach lot management or record-history
endpoints — those are supervisor tooling, not part of a QC agent's own
task workflow (see frontend/src/portals/customer/App.tsx's SUPERVISOR_ITEMS
comment for the UI-side counterpart of this restriction)."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lot, LotStatus, Portal, User, UserRole
from tests.conftest import token


async def _make_customer_supervisor(db: AsyncSession, seed) -> User:
    supervisor = User(
        tenant_id=seed["tenant"].id, organization_id=seed["cust_org"].id, email="custsup@test.com",
        keycloak_sub="sub-custsup", full_name="Customer Supervisor",
        role=UserRole.customer_supervisor, portal=Portal.customer, is_active=True,
    )
    db.add(supervisor)
    await db.commit()
    return supervisor


class TestLotEndpointsRoleGating:
    async def test_qc_agent_forbidden_from_listing_lots(self, db: AsyncSession, seed, client):
        qc_token = token(seed["qc_agent"], portal_override="customer")
        resp = await client.get(
            f"/api/lots/project/{seed['project'].id}",
            headers={"Authorization": f"Bearer {qc_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 403

    async def test_qc_agent_forbidden_from_lot_detail(self, db: AsyncSession, seed, client):
        lot = Lot(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Lot 1", status=LotStatus.released)
        db.add(lot)
        await db.commit()

        qc_token = token(seed["qc_agent"], portal_override="customer")
        resp = await client.get(
            f"/api/lots/{lot.id}",
            headers={"Authorization": f"Bearer {qc_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 403

    async def test_customer_supervisor_can_list_lots(self, db: AsyncSession, seed, client):
        supervisor = await _make_customer_supervisor(db, seed)
        sup_token = token(supervisor, portal_override="customer")
        resp = await client.get(
            f"/api/lots/project/{seed['project'].id}",
            headers={"Authorization": f"Bearer {sup_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 200


class TestRecordHistoryRoleGating:
    async def test_qc_agent_forbidden(self, db: AsyncSession, seed, client):
        qc_token = token(seed["qc_agent"], portal_override="customer")
        resp = await client.get(
            f"/api/records/{seed['record'].id}/history",
            headers={"Authorization": f"Bearer {qc_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 403

    async def test_customer_supervisor_allowed(self, db: AsyncSession, seed, client):
        supervisor = await _make_customer_supervisor(db, seed)
        sup_token = token(supervisor, portal_override="customer")
        resp = await client.get(
            f"/api/records/{seed['record'].id}/history",
            headers={"Authorization": f"Bearer {sup_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 200

    async def test_de_supervisor_still_allowed(self, db: AsyncSession, seed, client):
        sup_token = token(seed["supervisor"])
        resp = await client.get(
            f"/api/records/{seed['record'].id}/history",
            headers={"Authorization": f"Bearer {sup_token}", "X-Portal": "digitizing"},
        )
        assert resp.status_code == 200
