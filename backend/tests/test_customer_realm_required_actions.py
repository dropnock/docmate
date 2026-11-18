"""keycloak_service.ensure_customer_required_actions: customer realms must
enforce UPDATE_PASSWORD, or new users' temporary passwords never have to be
changed (the realm template used to register only CONFIGURE_TOTP)."""
import json

from app.services import keycloak_service


class _Resp:
    status_code = 204
    text = ""


class _Conn:
    def __init__(self, admin):
        self.admin, self.realm_name = admin, None

    def raw_post(self, path, data):
        self.admin.posts.append((path, json.loads(data)))
        self.admin.actions[json.loads(data)["providerId"]] = {
            "alias": json.loads(data)["providerId"], "enabled": False,
        }
        return _Resp()


class FakeAdmin:
    def __init__(self, actions):
        self.actions = {a["alias"]: a for a in actions}
        self.posts, self.updates = [], []
        self.connection = _Conn(self)

    def get_required_actions(self):
        return list(self.actions.values())

    def update_required_action(self, alias, payload):
        self.updates.append((alias, payload))
        self.actions[alias] = payload


def _patch(monkeypatch, admin):
    monkeypatch.setattr(keycloak_service, "_make_admin", lambda realm="master": admin)


def test_template_registers_update_password():
    by_alias = {a["alias"]: a for a in keycloak_service.CUSTOMER_REQUIRED_ACTIONS}
    assert by_alias["UPDATE_PASSWORD"]["enabled"] is True


def test_registers_and_enables_missing_update_password(monkeypatch):
    admin = FakeAdmin([{"alias": "CONFIGURE_TOTP", "enabled": False}, {"alias": "delete_account", "enabled": False}])
    _patch(monkeypatch, admin)

    assert keycloak_service.ensure_customer_required_actions("acme-archive") == ["UPDATE_PASSWORD"]
    assert admin.posts == [(
        "admin/realms/acme-archive/authentication/register-required-action",
        {"providerId": "UPDATE_PASSWORD", "name": "Update Password"},
    )]
    assert admin.actions["UPDATE_PASSWORD"]["enabled"] is True
    assert admin.actions["CONFIGURE_TOTP"]["enabled"] is False  # left alone


def test_enables_a_disabled_update_password(monkeypatch):
    admin = FakeAdmin([{"alias": "UPDATE_PASSWORD", "enabled": False, "priority": 30}])
    _patch(monkeypatch, admin)

    assert keycloak_service.ensure_customer_required_actions("acme-archive") == ["UPDATE_PASSWORD"]
    assert admin.posts == []
    assert admin.actions["UPDATE_PASSWORD"] == {"alias": "UPDATE_PASSWORD", "enabled": True, "priority": 30}


def test_no_change_when_already_enforced(monkeypatch):
    admin = FakeAdmin([{"alias": "UPDATE_PASSWORD", "enabled": True}])
    _patch(monkeypatch, admin)

    assert keycloak_service.ensure_customer_required_actions("acme-archive") == []
    assert admin.posts == [] and admin.updates == []
