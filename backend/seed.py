"""
First-run seed script — creates the initial tenant, orgs, and admin user.

Usage (from backend/):
    python seed.py
"""
import asyncio
from app.core.database import AsyncSessionLocal
from app.core.security import get_password_hash
from app.models.organization import Organization, OrgType
from app.models.tenant import Tenant
from app.models.user import Portal, User, UserRole


async def seed():
    async with AsyncSessionLocal() as db:
        # Tenant
        tenant = Tenant(name="Acme Digitizers", slug="acme")
        db.add(tenant)
        await db.flush()

        # Orgs
        de_org = Organization(tenant_id=tenant.id, name="Acme Scanning Ltd", type=OrgType.digitizing_entity)
        cust_org = Organization(tenant_id=tenant.id, name="Acme Archive Corp", type=OrgType.customer)
        db.add_all([de_org, cust_org])
        await db.flush()

        # Admin user (digitizing portal)
        admin = User(
            tenant_id=tenant.id,
            email="admin@acme.com",
            hashed_password=get_password_hash("changeme123"),
            full_name="System Admin",
            role=UserRole.admin,
            portal=Portal.digitizing,
            organization_id=de_org.id,
        )

        # DE Supervisor
        de_sup = User(
            tenant_id=tenant.id,
            email="supervisor@acme.com",
            hashed_password=get_password_hash("changeme123"),
            full_name="DE Supervisor",
            role=UserRole.de_supervisor,
            portal=Portal.digitizing,
            organization_id=de_org.id,
        )

        # DE Indexer
        indexer = User(
            tenant_id=tenant.id,
            email="indexer@acme.com",
            hashed_password=get_password_hash("changeme123"),
            full_name="John Indexer",
            role=UserRole.de_indexer,
            portal=Portal.digitizing,
            organization_id=de_org.id,
        )

        # Customer Supervisor
        cust_sup = User(
            tenant_id=tenant.id,
            email="qc-supervisor@archive.com",
            hashed_password=get_password_hash("changeme123"),
            full_name="Customer Supervisor",
            role=UserRole.customer_supervisor,
            portal=Portal.customer,
            organization_id=cust_org.id,
        )

        # Customer QC Agent
        qc_agent = User(
            tenant_id=tenant.id,
            email="qc-agent@archive.com",
            hashed_password=get_password_hash("changeme123"),
            full_name="Jane QC Agent",
            role=UserRole.customer_qc_agent,
            portal=Portal.customer,
            organization_id=cust_org.id,
        )

        db.add_all([admin, de_sup, indexer, cust_sup, qc_agent])
        await db.commit()

    print("Seeded:")
    print("  Tenant:  Acme Digitizers (slug=acme)")
    print("  admin@acme.com / changeme123  [admin, digitizing]")
    print("  supervisor@acme.com / changeme123  [de_supervisor, digitizing]")
    print("  indexer@acme.com / changeme123  [de_indexer, digitizing]")
    print("  qc-supervisor@archive.com / changeme123  [customer_supervisor, customer]")
    print("  qc-agent@archive.com / changeme123  [customer_qc_agent, customer]")


if __name__ == "__main__":
    asyncio.run(seed())
