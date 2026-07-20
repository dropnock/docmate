"""GET /projects/{id}/qc-agents must be scoped to the project's own customer
organization — a tenant can have multiple customer orgs (each onboarded
separately, see keycloak_service.create_customer_realm), and one customer's
supervisor must never see, let alone assign records to, another customer's
QC agents."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, OrgType, Portal, User, UserRole
from tests.conftest import token


class TestQcAgentsScoping:
    async def test_only_returns_agents_from_the_projects_own_customer_org(
        self, db: AsyncSession, seed, client
    ):
        # A second customer org in the same tenant, with its own supervisor
        # and QC agent — mirrors a real deployment serving multiple customers.
        other_org = Organization(
            tenant_id=seed["tenant"].id, name="Other Customer Org", type=OrgType.customer,
        )
        db.add(other_org)
        await db.flush()

        our_supervisor = User(
            tenant_id=seed["tenant"].id, organization_id=seed["cust_org"].id, email="oursup@test.com",
            keycloak_sub="sub-oursup", full_name="Our Supervisor",
            role=UserRole.customer_supervisor, portal=Portal.customer, is_active=True,
        )
        other_qc_agent = User(
            tenant_id=seed["tenant"].id, organization_id=other_org.id, email="otherqc@test.com",
            keycloak_sub="sub-otherqc", full_name="Other Org QC Agent",
            role=UserRole.customer_qc_agent, portal=Portal.customer, is_active=True,
        )
        db.add_all([our_supervisor, other_qc_agent])
        await db.commit()

        sup_token = token(our_supervisor, portal_override="customer")
        resp = await client.get(
            f"/api/projects/{seed['project'].id}/qc-agents",
            headers={"Authorization": f"Bearer {sup_token}"},
        )
        assert resp.status_code == 200
        names = {a["full_name"] for a in resp.json()}
        assert names == {seed["qc_agent"].full_name}
        assert other_qc_agent.full_name not in names

    async def test_supervisor_from_other_org_gets_404(self, db: AsyncSession, seed, client):
        other_org = Organization(
            tenant_id=seed["tenant"].id, name="Other Customer Org", type=OrgType.customer,
        )
        db.add(other_org)
        await db.flush()
        other_supervisor = User(
            tenant_id=seed["tenant"].id, organization_id=other_org.id, email="othersup@test.com",
            keycloak_sub="sub-othersup", full_name="Other Supervisor",
            role=UserRole.customer_supervisor, portal=Portal.customer, is_active=True,
        )
        db.add(other_supervisor)
        await db.commit()

        sup_token = token(other_supervisor, portal_override="customer")
        resp = await client.get(
            f"/api/projects/{seed['project'].id}/qc-agents",
            headers={"Authorization": f"Bearer {sup_token}"},
        )
        assert resp.status_code == 404
