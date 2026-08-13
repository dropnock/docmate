"""PATCH /projects/{id}/aql — role gating, audit trail, missing-config 404."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, AuditEntityType, AuditLog, Portal, Project, S3BucketStatus, User, UserRole
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


class TestUpdateAqlConfig:
    async def test_customer_supervisor_can_update(self, db: AsyncSession, seed, client):
        supervisor = await _make_customer_supervisor(db, seed)
        sup_token = token(supervisor, portal_override="customer")

        resp = await client.patch(
            f"/api/projects/{seed['project'].id}/aql",
            json={"normal_aql": 2.5, "sampling_mode": "manual"},
            headers={"Authorization": f"Bearer {sup_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["normal_aql"] == 2.5
        assert body["sampling_mode"] == "manual"

        result = await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.project,
                AuditLog.entity_id == seed["project"].id,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1
        assert events[0].action == AuditAction.status_changed
        assert events[0].performed_by == supervisor.id

    async def test_qc_agent_forbidden(self, db: AsyncSession, seed, client):
        qc_token = token(seed["qc_agent"], portal_override="customer")
        resp = await client.patch(
            f"/api/projects/{seed['project'].id}/aql",
            json={"normal_aql": 2.5},
            headers={"Authorization": f"Bearer {qc_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 403

    async def test_404_when_project_has_no_aql_config(self, db: AsyncSession, seed, client):
        supervisor = await _make_customer_supervisor(db, seed)
        project_no_config = Project(
            tenant_id=seed["tenant"].id, digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id, name="No Config Project",
            s3_bucket_status=S3BucketStatus.ready,
        )
        db.add(project_no_config)
        await db.commit()

        sup_token = token(supervisor, portal_override="customer")
        resp = await client.patch(
            f"/api/projects/{project_no_config.id}/aql",
            json={"normal_aql": 2.5},
            headers={"Authorization": f"Bearer {sup_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 404
