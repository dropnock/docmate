"""
One-time (idempotent) provisioning for the bulk-upload CLI's service account.

Ensures the docmate-cli Keycloak client exists, and that its service-account
user is mapped to a DocMate admin User row so scripts/bulk_upload.py can
authenticate non-interactively.

Run AFTER seed.py and AFTER Keycloak is up:
    python provision_cli_user.py

Safe to run repeatedly — never rotates the client secret, just prints it
again each time so an operator who lost it can recover it.
"""
import asyncio
import logging

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.organization import Organization, OrgType
from app.models.tenant import Tenant
from app.models.user import Portal, User, UserRole
from app.services.keycloak_service import (
    CLI_CLIENT_ID,
    CLI_REALM,
    _make_admin,
    ensure_cli_client,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CLI_EMAIL = "cli-bulk-upload@doc.local"
CLI_FULL_NAME = "Bulk Upload Service Account"


async def provision():
    secret = ensure_cli_client()

    admin = _make_admin("master")
    admin.connection.realm_name = CLI_REALM
    clients = admin.get_clients()
    cli_client = next(c for c in clients if c["clientId"] == CLI_CLIENT_ID)
    sa_user = admin.get_client_service_account_user(cli_client["id"])
    keycloak_sub = str(sa_user["id"])

    async with AsyncSessionLocal() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.slug == "doc"))).scalar_one_or_none()
        if not tenant:
            raise SystemExit("Tenant 'doc' not found — run seed.py first.")

        doc_org = (
            await db.execute(
                select(Organization).where(
                    Organization.tenant_id == tenant.id,
                    Organization.type == OrgType.digitizing_entity,
                )
            )
        ).scalar_one_or_none()
        if not doc_org:
            raise SystemExit("DOC organisation not found — run seed.py first.")

        user = (
            await db.execute(select(User).where(User.keycloak_sub == keycloak_sub))
        ).scalar_one_or_none()
        if not user:
            # Defensive fallback only — sub is stable from creation in practice.
            user = (
                await db.execute(select(User).where(User.email == CLI_EMAIL))
            ).scalar_one_or_none()

        if user:
            user.keycloak_sub = keycloak_sub
            user.role = UserRole.admin
            user.portal = Portal.digitizing
            user.organization_id = doc_org.id
            user.is_active = True
            action = "Updated"
        else:
            user = User(
                tenant_id=tenant.id,
                email=CLI_EMAIL,
                keycloak_sub=keycloak_sub,
                full_name=CLI_FULL_NAME,
                role=UserRole.admin,
                portal=Portal.digitizing,
                organization_id=doc_org.id,
                is_active=True,
            )
            db.add(user)
            action = "Created"

        await db.commit()

    print(f"\n{action} DB user: {CLI_EMAIL} (keycloak_sub={keycloak_sub}, role=admin, portal=digitizing)")
    print(f"Keycloak client: {CLI_CLIENT_ID} (realm={CLI_REALM})")
    print("\n" + "=" * 70)
    print("CLIENT SECRET (shown once — store it now, e.g. in a password manager")
    print("or as the DOCMATE_CLI_CLIENT_SECRET env var for bulk_upload.py):")
    print()
    print(f"    {secret}")
    print()
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(provision())
