"""
First-run seed script — creates the initial tenant, DOC org, a sample customer org,
and seeds users into both the database and Keycloak.

Run AFTER Keycloak is up:
    python seed.py
"""
import asyncio
import logging

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.organization import Organization, OrgType
from app.models.tenant import Tenant
from app.models.user import Portal, User, UserRole

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _try_keycloak(realm_slug: str, email: str, full_name: str, password: str) -> str | None:
    try:
        from app.services.keycloak_service import create_user_in_realm
        sub = create_user_in_realm(realm_slug, email, full_name, password)
        logger.info("  Keycloak user created: %s → %s", email, sub)
        return sub
    except Exception as exc:
        logger.warning("  Keycloak unavailable for %s (%s). Continuing without keycloak_sub.", email, exc)
        return None


def _try_create_realm(realm_slug: str, display_name: str) -> None:
    try:
        from app.services.keycloak_service import create_customer_realm
        create_customer_realm(realm_slug, display_name)
        logger.info("  Keycloak realm created: %s", realm_slug)
    except Exception as exc:
        logger.warning("  Could not create Keycloak realm %s: %s", realm_slug, exc)


async def seed():
    async with AsyncSessionLocal() as db:
        # ── Tenant ────────────────────────────────────────────────────────────
        tenant = Tenant(name="Digitizing Operations Centre", slug="doc")
        db.add(tenant)
        await db.flush()

        # ── Organisations ──────────────────────────────────────────────────────
        doc_org = Organization(
            tenant_id=tenant.id,
            name="DOC",
            type=OrgType.digitizing_entity,
            realm_slug=None,
        )
        cust_realm = "acme-archive"
        cust_org = Organization(
            tenant_id=tenant.id,
            name="Acme Archive Corp",
            type=OrgType.customer,
            realm_slug=cust_realm,
        )
        db.add_all([doc_org, cust_org])
        await db.flush()

        # ── Keycloak realms ────────────────────────────────────────────────────
        _try_create_realm(cust_realm, "Acme Archive Corp")

        # ── Users ──────────────────────────────────────────────────────────────
        user_specs = [
            dict(email="admin@doc.local",      full_name="System Admin",        role=UserRole.admin,               portal=Portal.digitizing, org_id=doc_org.id),
            dict(email="supervisor@doc.local",  full_name="DE Supervisor",       role=UserRole.de_supervisor,       portal=Portal.digitizing, org_id=doc_org.id),
            dict(email="indexer@doc.local",     full_name="John Indexer",        role=UserRole.de_indexer,          portal=Portal.digitizing, org_id=doc_org.id),
            dict(email="qa@doc.local",          full_name="QA Agent",            role=UserRole.de_qa_agent,         portal=Portal.digitizing, org_id=doc_org.id),
            dict(email="supervisor@acme.local", full_name="Customer Supervisor", role=UserRole.customer_supervisor,  portal=Portal.customer,   org_id=cust_org.id),
            dict(email="qc@acme.local",         full_name="Jane QC Agent",       role=UserRole.customer_qc_agent,   portal=Portal.customer,   org_id=cust_org.id),
        ]

        for spec in user_specs:
            realm = "doc" if spec["portal"] == Portal.digitizing else cust_realm
            sub = _try_keycloak(realm, spec["email"], spec["full_name"], settings.seed_default_password)
            db.add(User(
                tenant_id=tenant.id,
                email=spec["email"],
                keycloak_sub=sub,
                full_name=spec["full_name"],
                role=spec["role"],
                portal=spec["portal"],
                organization_id=spec["org_id"],
                is_active=True,
            ))

        await db.commit()

    print("\nSeeded successfully:")
    print("  Tenant: Digitizing Operations Centre (slug=doc)")
    print("  Orgs:   DOC [digitizing_entity]  |  Acme Archive Corp [customer, realm=acme-archive]")
    print()
    pw = settings.seed_default_password
    print("  DOC users (realm=doc):")
    print(f"    admin@doc.local / {pw}")
    print(f"    supervisor@doc.local / {pw}")
    print(f"    indexer@doc.local / {pw}")
    print(f"    qa@doc.local / {pw}")
    print()
    print("  Customer users (realm=acme-archive):")
    print(f"    supervisor@acme.local / {pw}")
    print(f"    qc@acme.local / {pw}")
    print()
    print("  All users will be prompted to set up TOTP on first login.")


if __name__ == "__main__":
    asyncio.run(seed())
