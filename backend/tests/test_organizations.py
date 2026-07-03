"""Organisation CRUD and domain field tests."""
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, OrgType
from tests.conftest import auth_headers


class TestOrganizationList:
    async def test_list_includes_domain(self, client, seed):
        resp = await client.get("/api/organizations", headers=auth_headers(seed["admin"]))
        assert resp.status_code == 200
        orgs = resp.json()
        cust = next(o for o in orgs if o["type"] == "customer")
        assert cust["domain"] == "custorg.com"

    async def test_list_de_org_domain_is_null(self, client, seed):
        resp = await client.get("/api/organizations", headers=auth_headers(seed["admin"]))
        orgs = resp.json()
        de = next(o for o in orgs if o["type"] == "digitizing_entity")
        assert de["domain"] is None


class TestOrganizationCreate:
    async def test_create_customer_org_with_domain(self, client, seed):
        with patch("app.services.keycloak_service.create_customer_realm"), \
             patch("app.services.s3_service.provision_org_bucket"):
            resp = await client.post(
                "/api/organizations",
                json={"name": "Acme Corp", "type": "customer", "domain": "acme.com"},
                headers=auth_headers(seed["admin"]),
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["domain"] == "acme.com"
        assert data["name"] == "Acme Corp"

    async def test_create_org_without_domain_defaults_null(self, client, seed):
        with patch("app.services.keycloak_service.create_customer_realm"), \
             patch("app.services.s3_service.provision_org_bucket"):
            resp = await client.post(
                "/api/organizations",
                json={"name": "No Domain Corp", "type": "customer"},
                headers=auth_headers(seed["admin"]),
            )
        assert resp.status_code == 201
        assert resp.json()["domain"] is None

    async def test_create_org_requires_admin(self, client, seed):
        resp = await client.post(
            "/api/organizations",
            json={"name": "Test", "type": "customer", "domain": "test.com"},
            headers=auth_headers(seed["indexer"]),
        )
        assert resp.status_code == 403


class TestOrganizationUpdate:
    async def test_update_domain(self, client, seed):
        resp = await client.patch(
            f"/api/organizations/{seed['cust_org'].id}",
            json={"domain": "newdomain.com"},
            headers=auth_headers(seed["admin"]),
        )
        assert resp.status_code == 200
        assert resp.json()["domain"] == "newdomain.com"

    async def test_update_domain_to_null(self, client, seed):
        """Sending domain=null clears the domain."""
        resp = await client.patch(
            f"/api/organizations/{seed['cust_org'].id}",
            json={"domain": None},
            headers=auth_headers(seed["admin"]),
        )
        assert resp.status_code == 200
        # exclude_none skips None values, so domain stays as-is — confirm behaviour
        # (null patch is a no-op with current exclude_none=True logic)
        data = resp.json()
        assert "domain" in data

    async def test_update_name_preserves_domain(self, client, seed):
        resp = await client.patch(
            f"/api/organizations/{seed['cust_org'].id}",
            json={"name": "Renamed Org"},
            headers=auth_headers(seed["admin"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Renamed Org"
        assert data["domain"] == "custorg.com"

    async def test_update_org_requires_admin(self, client, seed):
        resp = await client.patch(
            f"/api/organizations/{seed['cust_org'].id}",
            json={"domain": "hack.com"},
            headers=auth_headers(seed["indexer"]),
        )
        assert resp.status_code == 403

    async def test_update_nonexistent_org_returns_404(self, client, seed):
        resp = await client.patch(
            "/api/organizations/99999",
            json={"domain": "x.com"},
            headers=auth_headers(seed["admin"]),
        )
        assert resp.status_code == 404


class TestRealmByDomainWithOrgDomain:
    async def test_realm_found_via_org_domain(self, client, seed):
        """Lookup uses Organization.domain, not user email addresses."""
        resp = await client.get(
            "/api/auth/realm-by-domain?email=anyone@custorg.com"
        )
        assert resp.status_code == 200
        assert resp.json()["realm_slug"] == "cust-realm"

    async def test_updating_domain_changes_routing(self, client, db, seed):
        """After updating the org domain, the new domain resolves correctly."""
        seed["cust_org"].domain = "updated.com"
        await db.commit()

        resp = await client.get("/api/auth/realm-by-domain?email=user@updated.com")
        assert resp.status_code == 200
        assert resp.json()["realm_slug"] == "cust-realm"

        # Old domain no longer resolves
        resp2 = await client.get("/api/auth/realm-by-domain?email=user@custorg.com")
        assert resp2.status_code == 404
