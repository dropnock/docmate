"""Customer portal QC staff productivity: analytics_service.qc_staff_productivity
and its endpoint's role gating."""
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization, OrgType
from app.models.task import Task, TaskStatus, TaskType
from app.models.user import Portal, User, UserRole
from app.services import analytics_service
from tests.conftest import token


def _qc_task(seed, *, status, processing_time_seconds=None, completed_at=None):
    return Task(
        record_id=seed["record"].id, batch_id=seed["batch"].id, task_type=TaskType.qc,
        assigned_to=seed["qc_agent"].id, assigned_by=seed["supervisor"].id,
        status=status, processing_time_seconds=processing_time_seconds, completed_at=completed_at,
    )


class TestQcStaffProductivity:
    async def test_metrics_for_seeded_qc_agent(self, db: AsyncSession, seed):
        now = datetime.now(timezone.utc)
        db.add_all([
            _qc_task(seed, status=TaskStatus.completed, processing_time_seconds=60, completed_at=now),
            _qc_task(seed, status=TaskStatus.completed, processing_time_seconds=120, completed_at=now),
            _qc_task(seed, status=TaskStatus.failed),
            _qc_task(seed, status=TaskStatus.in_progress),
        ])
        await db.commit()

        rows = await analytics_service.qc_staff_productivity(db, project_id=seed["project"].id)
        assert len(rows) == 1
        row = rows[0]
        assert row["user_id"] == seed["qc_agent"].id
        assert row["qc"]["total_records_processed"] == 2
        assert row["qc"]["records_today"] == 2
        assert row["qc"]["avg_processing_time_seconds"] == 90
        assert row["qc"]["error_rate"] == round(1 / 3, 4)
        assert row["qc"]["tasks_in_progress"] == 1

    async def test_excludes_inactive_agent(self, db: AsyncSession, seed):
        seed["qc_agent"].is_active = False
        await db.commit()

        rows = await analytics_service.qc_staff_productivity(db, project_id=seed["project"].id)
        assert rows == []

    async def test_excludes_agent_from_other_customer_org(self, db: AsyncSession, seed):
        other_org = Organization(tenant_id=seed["tenant"].id, name="Other Cust Org", type=OrgType.customer)
        db.add(other_org)
        await db.flush()
        other_agent = User(
            tenant_id=seed["tenant"].id, organization_id=other_org.id, email="otherqc@test.com",
            keycloak_sub="sub-otherqc", full_name="Other QC Agent",
            role=UserRole.customer_qc_agent, portal=Portal.customer, is_active=True,
        )
        db.add(other_agent)
        await db.commit()

        rows = await analytics_service.qc_staff_productivity(db, project_id=seed["project"].id)
        assert [r["user_id"] for r in rows] == [seed["qc_agent"].id]


async def _make_customer_supervisor(db: AsyncSession, seed) -> User:
    supervisor = User(
        tenant_id=seed["tenant"].id, organization_id=seed["cust_org"].id, email="qcprod-sup@test.com",
        keycloak_sub="sub-qcprod-sup", full_name="Customer Supervisor",
        role=UserRole.customer_supervisor, portal=Portal.customer, is_active=True,
    )
    db.add(supervisor)
    await db.commit()
    return supervisor


class TestQcStaffProductivityEndpointRoleGating:
    async def test_customer_supervisor_allowed(self, db: AsyncSession, seed, client):
        supervisor = await _make_customer_supervisor(db, seed)
        sup_token = token(supervisor, portal_override="customer")
        resp = await client.get(
            "/api/analytics/qc-staff-productivity",
            params={"project_id": seed["project"].id},
            headers={"Authorization": f"Bearer {sup_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 200

    async def test_admin_allowed(self, db: AsyncSession, seed, client):
        admin_token = token(seed["admin"])
        resp = await client.get(
            "/api/analytics/qc-staff-productivity",
            params={"project_id": seed["project"].id},
            headers={"Authorization": f"Bearer {admin_token}", "X-Portal": "digitizing"},
        )
        assert resp.status_code == 200

    async def test_qc_agent_forbidden(self, db: AsyncSession, seed, client):
        qc_token = token(seed["qc_agent"], portal_override="customer")
        resp = await client.get(
            "/api/analytics/qc-staff-productivity",
            params={"project_id": seed["project"].id},
            headers={"Authorization": f"Bearer {qc_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 403

    async def test_de_supervisor_forbidden(self, db: AsyncSession, seed, client):
        sup_token = token(seed["supervisor"])
        resp = await client.get(
            "/api/analytics/qc-staff-productivity",
            params={"project_id": seed["project"].id},
            headers={"Authorization": f"Bearer {sup_token}", "X-Portal": "digitizing"},
        )
        assert resp.status_code == 403
