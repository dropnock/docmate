"""Auth endpoint and portal enforcement tests."""
from tests.conftest import token


class TestRealmBySubdomain:
    async def test_valid_subdomain_returns_realm(self, client, db, seed):
        seed["cust_org"].realm_slug = "nla"
        await db.commit()
        resp = await client.get("/api/auth/realm-by-subdomain?subdomain=nla")
        assert resp.status_code == 200
        data = resp.json()
        assert data["realm_slug"] == "nla"
        assert data["name"] == "Cust Org"

    async def test_unknown_subdomain_returns_404(self, client, seed):
        resp = await client.get("/api/auth/realm-by-subdomain?subdomain=unknown")
        assert resp.status_code == 404

    async def test_org_without_realm_slug_not_matched(self, client, seed):
        """Customer org with no realm_slug must not match any subdomain."""
        resp = await client.get("/api/auth/realm-by-subdomain?subdomain=cust-org")
        assert resp.status_code == 404

    async def test_digitizing_org_not_matched(self, client, db, seed):
        """Digitizing entity orgs must not be returned even if realm_slug matches."""
        seed["de_org"].realm_slug = "de-realm"
        await db.commit()
        resp = await client.get("/api/auth/realm-by-subdomain?subdomain=de-realm")
        assert resp.status_code == 404


class TestPortalEnforcement:
    async def test_portal_mismatch_rejected(self, client, seed):
        """JWT says 'digitizing' but X-Portal header says 'customer' → 403."""
        de_token = token(seed["admin"], portal_override="digitizing")
        resp = await client.get(
            "/api/users/me",
            headers={"Authorization": f"Bearer {de_token}", "X-Portal": "customer"},
        )
        assert resp.status_code == 403

    async def test_portal_match_allowed(self, client, seed):
        """JWT and X-Portal both say 'digitizing' → 200."""
        de_token = token(seed["admin"])
        resp = await client.get(
            "/api/users/me",
            headers={"Authorization": f"Bearer {de_token}", "X-Portal": "digitizing"},
        )
        assert resp.status_code == 200

    async def test_no_portal_header_allowed(self, client, seed):
        """No X-Portal header at all → check skipped, request succeeds."""
        de_token = token(seed["admin"])
        resp = await client.get(
            "/api/users/me",
            headers={"Authorization": f"Bearer {de_token}"},
        )
        assert resp.status_code == 200

    async def test_unauthenticated_request_rejected(self, client, seed):
        resp = await client.get("/api/users/me")
        assert resp.status_code == 401

    async def test_insufficient_role_rejected(self, client, seed):
        """An indexer cannot create a user (admin-only)."""
        indexer_token = token(seed["indexer"])
        resp = await client.post(
            "/api/users",
            json={
                "email": "new@test.com", "password": "pass", "full_name": "New",
                "role": "de_staff", "portal": "digitizing",
            },
            headers={"Authorization": f"Bearer {indexer_token}"},
        )
        assert resp.status_code == 403
