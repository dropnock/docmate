import logging
import re
from urllib.parse import urlparse

from keycloak import KeycloakAdmin, KeycloakOpenIDConnection

from app.core.config import settings

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


def create_customer_realm(realm_slug: str, display_name: str) -> None:
    admin = _make_admin("master")
    admin.create_realm(
        {
            "realm": realm_slug,
            "displayName": display_name,
            "enabled": True,
            "registrationAllowed": False,
            "loginWithEmailAllowed": True,
            "otpPolicyType": "totp",
            "otpPolicyAlgorithm": "HmacSHA1",
            "otpPolicyDigits": 6,
            "otpPolicyPeriod": 30,
            "requiredActions": [
                {
                    "alias": "CONFIGURE_TOTP",
                    "name": "Configure OTP",
                    "providerId": "CONFIGURE_TOTP",
                    "enabled": False,
                    "defaultAction": False,
                    "priority": 10,
                    "config": {},
                }
            ],
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
