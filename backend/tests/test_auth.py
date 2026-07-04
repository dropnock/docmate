"""Auth endpoint and portal enforcement tests."""
from tests.conftest import token


class TestLogin:
    async def test_login_success(self, client, seed):
        resp = await client.post("/api/auth/login", json={
            "email": "admin@test.com",
            "password": "pass123",
            "portal": "digitizing",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["portal"] == "digitizing"

    async def test_login_wrong_password(self, client, seed):
        resp = await client.post("/api/auth/login", json={
            "email": "admin@test.com",
            "password": "wrong",
            "portal": "digitizing",
        })
        assert resp.status_code == 401

    async def test_login_wrong_portal(self, client, seed):
        # admin is on digitizing portal; logging in via customer portal should fail
        resp = await client.post("/api/auth/login", json={
            "email": "admin@test.com",
            "password": "pass123",
            "portal": "customer",
        })
        assert resp.status_code in (401, 403)

    async def test_login_unknown_email(self, client, seed):
        resp = await client.post("/api/auth/login", json={
            "email": "nobody@nowhere.com",
            "password": "pass123",
            "portal": "digitizing",
        })
        assert resp.status_code == 401


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
                "role": "de_indexer", "portal": "digitizing",
            },
            headers={"Authorization": f"Bearer {indexer_token}"},
        )
        assert resp.status_code == 403
