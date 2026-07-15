import app.services.keycloak_service as keycloak_service
from app.core.config import settings
from app.services.keycloak_service import _build_de_client


class _FakeAdvisoryLockSession:
    """Fakes AsyncSessionLocal()'s `async with ... as db:` usage, recording
    the raw SQL text of every execute() call so tests can assert the
    advisory lock was actually taken and released."""

    def __init__(self, calls):
        self._calls = calls

    async def execute(self, stmt, params=None):
        self._calls.append((str(stmt), params))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeAdminConnection:
    realm_name = None


class _FakeAdmin:
    """Fakes KeycloakAdmin just enough for sync_de_client(): a single
    existing docmate-de client, and a recorder for update_client() calls."""

    def __init__(self, de_client):
        self.connection = _FakeAdminConnection()
        self._de_client = de_client
        self.update_calls = []

    def get_clients(self):
        return [self._de_client]

    def update_client(self, client_id, payload):
        self.update_calls.append((client_id, payload))


def test_build_de_client_dedupes_repeated_base_url(monkeypatch):
    """A duplicated (or slash-inconsistent) entry in DE_PORTAL_BASE_URLS must
    not produce a duplicate redirectUris entry — Keycloak's update_client()
    does a bare per-entry INSERT on that collection, and a duplicate value
    trips the DB's unique constraint, rolling back the whole update."""
    monkeypatch.setattr(
        settings,
        "de_portal_base_urls",
        "https://digitizing.docmate.local,https://digitizing.docmate.local/",
    )

    client = _build_de_client()

    assert len(client["redirectUris"]) == len(set(client["redirectUris"]))
    assert client["redirectUris"].count("https://digitizing.docmate.local/") == 1


def test_build_de_client_includes_each_distinct_base_url(monkeypatch):
    monkeypatch.setattr(
        settings,
        "de_portal_base_urls",
        "https://www.docmate.local,https://digitizing.docmate.local",
    )

    client = _build_de_client()

    assert "https://www.docmate.local" in client["redirectUris"]
    assert "https://digitizing.docmate.local" in client["redirectUris"]


async def test_sync_de_client_skips_update_when_already_in_sync(monkeypatch):
    """Every gunicorn worker calls sync_de_client() at startup. If the
    client's redirect URIs already match what we'd set, update_client() must
    not be called at all — this is what keeps concurrent workers from
    racing on Keycloak's collection-recreate and tripping the DB's unique
    constraint (the workers still serialize via the advisory lock, but the
    one that acquires the lock second/third/fourth must no-op)."""
    monkeypatch.setattr(settings, "de_portal_base_urls", "https://digitizing.docmate.local")
    desired = _build_de_client()
    de_client = {
        "id": "abc-123",
        "clientId": "docmate-de",
        "redirectUris": desired["redirectUris"],
        "attributes": {"post.logout.redirect.uris": desired["attributes"]["post.logout.redirect.uris"]},
    }
    fake_admin = _FakeAdmin(de_client)
    monkeypatch.setattr(keycloak_service, "_make_admin", lambda realm="master": fake_admin)

    session_calls = []
    monkeypatch.setattr(keycloak_service, "AsyncSessionLocal", lambda: _FakeAdvisoryLockSession(session_calls))

    await keycloak_service.sync_de_client()

    assert fake_admin.update_calls == []
    assert any("pg_advisory_lock" in stmt for stmt, _ in session_calls)
    assert any("pg_advisory_unlock" in stmt for stmt, _ in session_calls)


async def test_sync_de_client_updates_when_out_of_sync(monkeypatch):
    """When the client's redirect URIs genuinely differ (e.g. a real
    DE_PORTAL_BASE_URLS change), the first worker to acquire the lock must
    still perform the update."""
    monkeypatch.setattr(settings, "de_portal_base_urls", "https://digitizing.docmate.local")
    de_client = {
        "id": "abc-123",
        "clientId": "docmate-de",
        "redirectUris": ["https://stale.example.com"],
        "attributes": {"post.logout.redirect.uris": "https://stale.example.com/*"},
    }
    fake_admin = _FakeAdmin(de_client)
    monkeypatch.setattr(keycloak_service, "_make_admin", lambda realm="master": fake_admin)
    monkeypatch.setattr(keycloak_service, "AsyncSessionLocal", lambda: _FakeAdvisoryLockSession([]))

    await keycloak_service.sync_de_client()

    assert len(fake_admin.update_calls) == 1
    client_id, payload = fake_admin.update_calls[0]
    assert client_id == "abc-123"
    assert payload["clientId"] == "docmate-de"
    assert "https://digitizing.docmate.local" in payload["redirectUris"]
