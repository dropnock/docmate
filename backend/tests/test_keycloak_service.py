from app.core.config import settings
from app.services.keycloak_service import _build_de_client


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
