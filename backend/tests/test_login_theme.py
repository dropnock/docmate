"""keycloak_service.ensure_login_theme: realms' sign-in pages use the
DocMate theme (keycloak/themes/docmate)."""
from app.core.config import settings
from app.services import keycloak_service


class FakeAdmin:
    def __init__(self, realms):
        self.realms, self.updates = realms, []

    def get_realm(self, realm):
        return self.realms[realm]

    def update_realm(self, realm, payload):
        self.updates.append((realm, payload))
        self.realms[realm].update(payload)


def _patch(monkeypatch, admin):
    monkeypatch.setattr(keycloak_service, "_make_admin", lambda realm="master": admin)


def test_sets_theme_when_missing(monkeypatch):
    admin = FakeAdmin({"acme": {"realm": "acme"}})
    _patch(monkeypatch, admin)
    assert keycloak_service.ensure_login_theme("acme") is True
    assert admin.updates == [("acme", {"loginTheme": "docmate"})]


def test_no_update_when_already_set(monkeypatch):
    admin = FakeAdmin({"acme": {"realm": "acme", "loginTheme": "docmate"}})
    _patch(monkeypatch, admin)
    assert keycloak_service.ensure_login_theme("acme") is False
    assert admin.updates == []


def test_empty_setting_leaves_realms_alone(monkeypatch):
    monkeypatch.setattr(settings, "keycloak_login_theme", "")
    admin = FakeAdmin({"acme": {"realm": "acme", "loginTheme": "keycloak"}})
    _patch(monkeypatch, admin)
    assert keycloak_service.ensure_login_theme("acme") is False
    assert admin.updates == []


def test_new_customer_realm_gets_theme(monkeypatch):
    created = {}

    class Admin:
        connection = type("C", (), {"realm_name": None})()

        def create_realm(self, payload, skip_exists):
            created.update(payload)

        def create_client(self, payload, skip_exists):
            pass

    _patch(monkeypatch, Admin())
    keycloak_service.create_customer_realm("acme", "Acme")
    assert created["loginTheme"] == "docmate"
