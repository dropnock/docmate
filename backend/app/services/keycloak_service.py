import json
import logging
import re
from urllib.parse import urlparse

from keycloak import KeycloakAdmin, KeycloakOpenIDConnection
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _build_customer_client(realm_slug: str) -> dict:
    """Build the docmate-cust client config with redirect URIs for this realm's subdomain."""
    parsed = urlparse(settings.customer_portal_base_url)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "localhost"
    port = parsed.port

    subdomain_origin = f"{scheme}://{realm_slug}.{host}"
    if port and not (scheme == "http" and port == 80) and not (scheme == "https" and port == 443):
        subdomain_origin += f":{port}"

    dev_uris = [
        "http://localhost:8080/*",
        "http://localhost:5174/*",
    ]
    subdomain_uris = [subdomain_origin, f"{subdomain_origin}/", f"{subdomain_origin}/*"]

    return {
        "clientId": "docmate-cust",
        "name": "DocMate Customer Portal",
        "enabled": True,
        "publicClient": True,
        "bearerOnly": False,
        "consentRequired": False,
        "standardFlowEnabled": True,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "protocol": "openid-connect",
        "redirectUris": subdomain_uris + dev_uris,
        "webOrigins": ["+"],
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "post.logout.redirect.uris": "##".join(
                [f"{subdomain_origin}/*"] + dev_uris
            ),
        },
    }


DE_REALM = "doc"
DE_CLIENT_ID = "docmate-de"


def _build_de_client() -> dict:
    """Build the docmate-de client config with redirect URIs for every
    configured base URL (settings.de_portal_base_urls, comma-separated)."""
    dev_uris = [
        "http://localhost:5173",
        "http://localhost:5173/",
        "http://localhost:5173/*",
        "http://localhost:80",
        "http://localhost:80/",
        "http://localhost:80/*",
        "http://localhost",
        "http://localhost/",
        "http://localhost/*",
        "http://localhost:8080",
        "http://localhost:8080/",
        "http://localhost:8080/*",
    ]

    # Dedupe: a repeated or equivalent-after-normalization entry in
    # DE_PORTAL_BASE_URLS would otherwise put the same value twice in
    # redirectUris, and Keycloak's collection-recreate on update_client()
    # does a bare INSERT per entry — a duplicate trips the DB's unique
    # constraint, the whole client update rolls back, and none of the
    # intended redirect URIs (including any newly added hostname) take effect.
    base_urls = list(dict.fromkeys(u.strip().rstrip("/") for u in settings.de_portal_base_urls.split(",") if u.strip()))
    configured_uris: list[str] = []
    for base_url in base_urls:
        configured_uris += [base_url, f"{base_url}/", f"{base_url}/*"]

    return {
        "clientId": DE_CLIENT_ID,
        "name": "DocMate Digitizing Portal",
        "enabled": True,
        "publicClient": True,
        "bearerOnly": False,
        "consentRequired": False,
        "standardFlowEnabled": True,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "protocol": "openid-connect",
        "redirectUris": list(dict.fromkeys(configured_uris + dev_uris)),
        "webOrigins": ["+"],
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "post.logout.redirect.uris": "##".join(
                list(dict.fromkeys([f"{u}/*" for u in base_urls] + dev_uris))
            ),
        },
    }


# Arbitrary fixed key for a Postgres session-level advisory lock — see
# sync_de_client() for why this is needed. Any constant bigint works; it
# just has to not collide with a lock key used elsewhere in the app.
_DE_CLIENT_SYNC_LOCK_KEY = 8_791_234_002_517


async def sync_de_client() -> None:
    """Idempotently reconcile the docmate-de client's redirect URIs with the
    currently configured DE_PORTAL_BASE_URLS.

    Safe to call on every backend startup: Keycloak's `--import-realm` only
    imports a realm the first time it's seen, so once the realm exists,
    changing hostnames requires this to actually take effect.

    Called from every gunicorn worker's own lifespan startup, so multiple
    processes call this concurrently on every deploy. Keycloak's
    update_client() does a Hibernate collection-recreate (delete all
    redirect_uris rows, then a bare INSERT per entry, no upsert) — two
    workers racing on that trips the DB's unique constraint and crashes the
    whole update for both. A Postgres advisory lock on our own DB (not
    Keycloak's) serializes the workers, and the current-vs-desired check
    below means only the one worker that actually observes a real change
    ever calls update_client() at all — the rest no-op once they see the
    prior worker's update already landed.
    """
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _DE_CLIENT_SYNC_LOCK_KEY})
        try:
            try:
                if ensure_login_theme(DE_REALM):
                    logger.info("Set login theme %s on realm %s", settings.keycloak_login_theme, DE_REALM)
            except Exception:
                logger.exception("Could not set the login theme on realm %s", DE_REALM)
            admin = _make_admin("master")
            admin.connection.realm_name = DE_REALM
            clients = admin.get_clients()
            de_client = next((c for c in clients if c["clientId"] == DE_CLIENT_ID), None)
            if not de_client:
                logger.warning("%s client not found in realm %s; skipping redirect URI sync", DE_CLIENT_ID, DE_REALM)
                return

            desired = _build_de_client()
            current_uris = set(de_client.get("redirectUris") or [])
            desired_uris = set(desired["redirectUris"])
            current_logout = (de_client.get("attributes") or {}).get("post.logout.redirect.uris", "")
            desired_logout = desired["attributes"]["post.logout.redirect.uris"]
            if current_uris == desired_uris and current_logout == desired_logout:
                logger.info("%s client redirect URIs already in sync; skipping update", DE_CLIENT_ID)
                return

            admin.update_client(de_client["id"], desired)
            logger.info("Synced redirect URIs for %s client", DE_CLIENT_ID)
        finally:
            await db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _DE_CLIENT_SYNC_LOCK_KEY})


CLI_REALM = "doc"
CLI_CLIENT_ID = "docmate-cli"


def _build_cli_client() -> dict:
    """Config for the docmate-cli service-account client (scripts/bulk_upload.py).

    No interactive login, no human credentials — machine-to-machine only.
    Mirrors the same client definition in keycloak/realms/doc-realm.json so
    a fresh import and this idempotent runtime path never drift apart.
    """
    return {
        "clientId": CLI_CLIENT_ID,
        "name": "DocMate Bulk Upload CLI",
        "description": (
            "Service account for scripts/bulk_upload.py — no interactive login, "
            "no human credentials. Secret is auto-generated by Keycloak; "
            "retrieve it via provision_cli_user.py."
        ),
        "enabled": True,
        "publicClient": False,
        "bearerOnly": False,
        "consentRequired": False,
        "standardFlowEnabled": False,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": True,
        "protocol": "openid-connect",
    }


def ensure_cli_client() -> str:
    """Idempotently ensure the docmate-cli service-account client exists in the
    doc realm, and return its client secret.

    Handles both cases: the realm was imported fresh with docmate-cli already
    in doc-realm.json (client exists, Keycloak auto-generated a secret at
    import time), and the realm pre-existed before this client was added
    (--import-realm only imports on first realm creation — see
    sync_de_client()'s docstring for the same constraint), in which case the
    client is created here and Keycloak auto-generates the secret then.

    Never rotates an already-existing client's secret — just reads and
    returns its current value, so this is safe to call on every startup or
    provisioning run without invalidating a secret an operator already has.
    """
    admin = _make_admin("master")
    admin.connection.realm_name = CLI_REALM
    clients = admin.get_clients()
    cli_client = next((c for c in clients if c["clientId"] == CLI_CLIENT_ID), None)

    if not cli_client:
        client_uuid = admin.create_client(_build_cli_client(), skip_exists=True)
        if not client_uuid:
            clients = admin.get_clients()
            cli_client = next(c for c in clients if c["clientId"] == CLI_CLIENT_ID)
            client_uuid = cli_client["id"]
        logger.info("Created %s client in realm %s", CLI_CLIENT_ID, CLI_REALM)
    else:
        client_uuid = cli_client["id"]
        # Reconcile config in case it drifted — same idempotent-update pattern
        # as sync_de_client(). Never touches the secret.
        admin.update_client(client_uuid, _build_cli_client())
        logger.info("%s client already exists in realm %s; config reconciled", CLI_CLIENT_ID, CLI_REALM)

    secret_data = admin.get_client_secrets(client_uuid)
    return secret_data["value"]


def slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:60]


def _make_admin(realm: str = "master") -> KeycloakAdmin:
    conn = KeycloakOpenIDConnection(
        server_url=settings.keycloak_internal_url.rstrip("/") + "/",
        username=settings.keycloak_admin_user,
        password=settings.keycloak_admin_password,
        realm_name=realm,
        verify=False,
    )
    return KeycloakAdmin(connection=conn)


# Required actions a customer realm registers. Creating a realm with an
# explicit requiredActions list registers only these, not Keycloak's
# defaults, so UPDATE_PASSWORD must be listed: without it the
# requiredActions=["UPDATE_PASSWORD"] create_user_in_realm puts on every new
# user is silently ignored and temporary passwords are never changed.
CUSTOMER_REQUIRED_ACTIONS = [
    {
        "alias": "CONFIGURE_TOTP",
        "name": "Configure OTP",
        "providerId": "CONFIGURE_TOTP",
        "enabled": False,
        "defaultAction": False,
        "priority": 10,
        "config": {},
    },
    {
        "alias": "UPDATE_PASSWORD",
        "name": "Update Password",
        "providerId": "UPDATE_PASSWORD",
        "enabled": True,
        "defaultAction": False,
        "priority": 30,
        "config": {},
    },
]


def ensure_customer_required_actions(realm_slug: str) -> list[str]:
    """Register and enable the required actions a customer realm needs
    (CUSTOMER_REQUIRED_ACTIONS entries with enabled=True). Realms created
    before UPDATE_PASSWORD was added to the template lack it. Idempotent;
    returns the aliases it changed."""
    admin = _make_admin("master")
    admin.connection.realm_name = realm_slug
    current = {a["alias"]: a for a in admin.get_required_actions()}
    changed = []
    for spec in CUSTOMER_REQUIRED_ACTIONS:
        if not spec["enabled"]:
            continue
        alias = spec["alias"]
        if alias not in current:
            resp = admin.connection.raw_post(
                f"admin/realms/{realm_slug}/authentication/register-required-action",
                data=json.dumps({"providerId": spec["providerId"], "name": spec["name"]}),
            )
            if resp.status_code not in (200, 201, 204, 409):  # 409: another worker registered it
                raise RuntimeError(f"register {alias} in {realm_slug}: HTTP {resp.status_code} {resp.text[:200]}")
            admin.update_required_action(alias, {**spec})
            changed.append(alias)
        elif not current[alias].get("enabled"):
            admin.update_required_action(alias, {**current[alias], "enabled": True})
            changed.append(alias)
    return changed


def ensure_login_theme(realm: str) -> bool:
    """Point the realm's sign-in pages at settings.keycloak_login_theme.
    --import-realm only applies doc-realm.json to a new realm, and customer
    realms made before the theme existed have none, so this runs at startup.
    Idempotent; returns whether it changed anything."""
    theme = settings.keycloak_login_theme
    if not theme:
        return False
    admin = _make_admin("master")
    if admin.get_realm(realm).get("loginTheme") == theme:
        return False
    admin.update_realm(realm, {"loginTheme": theme})
    return True


_CUSTOMER_REALM_SYNC_LOCK_KEY = 8_791_234_002_518


async def sync_customer_realms() -> None:
    """Startup: make sure every customer realm enforces UPDATE_PASSWORD (see
    CUSTOMER_REQUIRED_ACTIONS). Serialised across gunicorn workers with an
    advisory lock, like sync_de_client; one bad realm doesn't stop the rest."""
    from sqlalchemy import select

    from app.models.organization import Organization

    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _CUSTOMER_REALM_SYNC_LOCK_KEY})
        try:
            slugs = (await db.execute(
                select(Organization.realm_slug).where(Organization.realm_slug.is_not(None)).distinct()
            )).scalars().all()
            for slug in slugs:
                try:
                    changed = ensure_customer_required_actions(slug)
                    if changed:
                        logger.info("Enabled required actions %s in customer realm %s", changed, slug)
                except Exception:
                    logger.exception("Could not check required actions in customer realm %s", slug)
                try:
                    if ensure_login_theme(slug):
                        logger.info("Set login theme %s on customer realm %s", settings.keycloak_login_theme, slug)
                except Exception:
                    logger.exception("Could not set the login theme on customer realm %s", slug)
        finally:
            await db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _CUSTOMER_REALM_SYNC_LOCK_KEY})


def create_customer_realm(realm_slug: str, display_name: str) -> None:
    admin = _make_admin("master")
    admin.create_realm(
        {
            "realm": realm_slug,
            "displayName": display_name,
            "enabled": True,
            "registrationAllowed": False,
            "loginWithEmailAllowed": True,
            # Explicit, not just relying on the Keycloak factory default (also
            # 1800s) — see doc-realm.json's matching setting and
            # frontend/src/shared/api/keycloak.ts's startSessionKeepAlive for
            # why this needs to be a known, version-controlled value rather
            # than an implicit platform default.
            "ssoSessionIdleTimeout": 1800,
            "otpPolicyType": "totp",
            "otpPolicyAlgorithm": "HmacSHA1",
            "otpPolicyDigits": 6,
            "otpPolicyPeriod": 30,
            "requiredActions": CUSTOMER_REQUIRED_ACTIONS,
            **({"loginTheme": settings.keycloak_login_theme} if settings.keycloak_login_theme else {}),
        },
        skip_exists=True,
    )
    # Create the customer portal client in the new realm
    admin.connection.realm_name = realm_slug
    admin.create_client(_build_customer_client(realm_slug), skip_exists=True)
    logger.info("Provisioned Keycloak realm: %s", realm_slug)


def update_customer_client_uris(realm_slug: str) -> None:
    """Patch an existing realm's docmate-cust client with the current redirect URIs.

    Call this for realms created before the subdomain routing change.
    """
    admin = _make_admin("master")
    admin.connection.realm_name = realm_slug
    clients = admin.get_clients()
    cust_client = next((c for c in clients if c["clientId"] == "docmate-cust"), None)
    if not cust_client:
        logger.warning("docmate-cust client not found in realm %s", realm_slug)
        return
    admin.update_client(cust_client["id"], _build_customer_client(realm_slug))
    logger.info("Updated redirect URIs for realm: %s", realm_slug)


def create_user_in_realm(
    realm_slug: str, email: str, full_name: str, temp_password: str
) -> str:
    """Create user in Keycloak and return the Keycloak UUID (sub claim)."""
    admin = _make_admin("master")
    admin.connection.realm_name = realm_slug

    parts = full_name.split(" ", 1)
    user_id = admin.create_user(
        {
            "username": email,
            "email": email,
            "firstName": parts[0],
            "lastName": parts[1] if len(parts) > 1 else "",
            "enabled": True,
            "emailVerified": True,
            "requiredActions": ["UPDATE_PASSWORD"],
            "credentials": [
                {"type": "password", "value": temp_password, "temporary": True}
            ],
        },
        exist_ok=True,
    )
    return str(user_id)


def set_user_enabled(realm_slug: str, keycloak_sub: str, enabled: bool) -> None:
    admin = _make_admin("master")
    admin.connection.realm_name = realm_slug
    admin.update_user(keycloak_sub, {"enabled": enabled})
