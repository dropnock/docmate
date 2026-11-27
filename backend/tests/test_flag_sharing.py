"""Sharing flagged records with the customer (flag_service.share_flags and
friends): what a customer supervisor can see, the discussion on a shared
flag, the notifications both sides get, and the audit trail."""
import csv
import io

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditAction, AuditLog, Notification, Organization, OrgType, Portal, TaskType, User, UserRole,
)
from app.services import task_service
from tests.conftest import token


def _de(user) -> dict:
    return {"Authorization": f"Bearer {token(user)}", "X-Portal": "digitizing"}


def _cust(user) -> dict:
    return {"Authorization": f"Bearer {token(user)}", "X-Portal": "customer"}


async def _people(db: AsyncSession, seed) -> dict:
    """A second DE supervisor, a customer supervisor of the project's customer
    org, and one from another customer org."""
    other_org = Organization(tenant_id=seed["tenant"].id, name="Other Cust", type=OrgType.customer)
    db.add(other_org)
    await db.flush()
    sup2 = User(tenant_id=seed["tenant"].id, organization_id=seed["de_org"].id, email="sup2@test.com",
                keycloak_sub="sub-sup2", full_name="Supervisor Two", role=UserRole.de_supervisor,
                portal=Portal.digitizing, is_active=True)
    cust = User(tenant_id=seed["tenant"].id, organization_id=seed["cust_org"].id, email="csup@test.com",
                keycloak_sub="sub-csup", full_name="Customer Sup", role=UserRole.customer_supervisor,
                portal=Portal.customer, is_active=True)
    outsider = User(tenant_id=seed["tenant"].id, organization_id=other_org.id, email="osup@test.com",
                    keycloak_sub="sub-osup", full_name="Other Sup", role=UserRole.customer_supervisor,
                    portal=Portal.customer, is_active=True)
    db.add_all([sup2, cust, outsider])
    await db.commit()
    return {"sup": seed["supervisor"], "sup2": sup2, "cust": cust, "outsider": outsider}


async def _flag(db: AsyncSession, seed, client, record, reason="Owner name cut off") -> int:
    await task_service.assign_task(
        db, record_id=record.id, batch_id=seed["batch"].id, task_type=TaskType.indexing,
        agent_id=seed["indexer"].id, supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )
    await db.commit()
    resp = await client.put(f"/api/records/{record.id}/flag", json={"reason": reason}, headers=_de(seed["indexer"]))
    return resp.json()["id"]


async def _share(client, seed, flag_ids, who=None):
    return await client.post(
        f"/api/projects/{seed['project'].id}/flags/share", json={"flag_ids": flag_ids}, headers=_de(who or seed["supervisor"]),
    )


async def _notices(db: AsyncSession) -> list[tuple[int, str, int | None]]:
    rows = (await db.execute(select(Notification).order_by(Notification.id))).scalars().all()
    return [(n.user_id, n.kind, n.flag_id) for n in rows]


def _list_url(seed) -> str:
    return f"/api/projects/{seed['project'].id}/flags"


class TestSharing:
    async def test_customer_sees_only_shared_flags(self, db: AsyncSession, seed, client):
        p = await _people(db, seed)
        f1 = await _flag(db, seed, client, seed["record"])
        f2 = await _flag(db, seed, client, seed["record2"], "Second issue")

        assert (await client.get(_list_url(seed), headers=_cust(p["cust"]))).json() == []
        assert (await client.get(f"/api/flags/{f1}", headers=_cust(p["cust"]))).status_code == 404
        state = (await client.get(f"/api/records/{seed['record'].id}/flag", headers=_cust(p["cust"]))).json()
        assert state == {"open": None, "last_resolved": None}

        resp = await _share(client, seed, [f1])
        assert resp.status_code == 200, resp.text
        rows = (await client.get(_list_url(seed), headers=_cust(p["cust"]))).json()
        assert [(r["id"], r["shared_by_name"]) for r in rows] == [(f1, "Supervisor")]
        assert (await client.get(f"/api/flags/{f1}", headers=_cust(p["cust"]))).json()["reason"] == "Owner name cut off"
        assert (await client.get(f"/api/flags/{f2}", headers=_cust(p["cust"]))).status_code == 404
        state = (await client.get(f"/api/records/{seed['record'].id}/flag", headers=_cust(p["cust"]))).json()
        assert state["open"]["id"] == f1

        # the DE side still sees everything, with the shared marker
        rows = {r["id"]: r for r in (await client.get(_list_url(seed), headers=_de(p["sup"]))).json()}
        assert rows[f1]["shared_at"] is not None and rows[f2]["shared_at"] is None

    async def test_customer_of_another_org_sees_nothing(self, db: AsyncSession, seed, client):
        p = await _people(db, seed)
        f1 = await _flag(db, seed, client, seed["record"])
        await _share(client, seed, [f1])
        assert (await client.get(f"/api/flags/{f1}", headers=_cust(p["outsider"]))).status_code == 404
        assert (await client.get(_list_url(seed), headers=_cust(p["outsider"]))).status_code == 404

    async def test_only_digitizing_supervisors_share_unshare_and_resolve(self, db: AsyncSession, seed, client):
        p = await _people(db, seed)
        f1 = await _flag(db, seed, client, seed["record"])
        assert (await _share(client, seed, [f1], who=seed["indexer"])).status_code == 403
        resp = await client.post(f"/api/projects/{seed['project'].id}/flags/share", json={"flag_ids": [f1]}, headers=_cust(p["cust"]))
        assert resp.status_code == 403
        await _share(client, seed, [f1])
        assert (await client.post(f"/api/flags/{f1}/unshare", headers=_cust(p["cust"]))).status_code == 403
        assert (await client.post(f"/api/flags/{f1}/resolve", json={"resolution": "x"}, headers=_cust(p["cust"]))).status_code == 403

    async def test_only_open_flags_can_be_shared(self, db: AsyncSession, seed, client):
        f1 = await _flag(db, seed, client, seed["record"])
        await client.post(f"/api/flags/{f1}/resolve", json={"resolution": "done"}, headers=_de(seed["supervisor"]))
        assert (await _share(client, seed, [f1])).status_code == 409
        assert (await _share(client, seed, [999])).status_code == 404

    async def test_unsharing_hides_it_again(self, db: AsyncSession, seed, client):
        p = await _people(db, seed)
        f1 = await _flag(db, seed, client, seed["record"])
        await _share(client, seed, [f1])
        resp = await client.post(f"/api/flags/{f1}/unshare", headers=_de(p["sup"]))
        assert resp.status_code == 200, resp.text
        assert (await client.get(_list_url(seed), headers=_cust(p["cust"]))).json() == []
        assert (await client.get(f"/api/flags/{f1}", headers=_cust(p["cust"]))).status_code == 404
        assert (await client.post(f"/api/flags/{f1}/unshare", headers=_de(p["sup"]))).status_code == 409


class TestDiscussionAndNotifications:
    async def test_share_comment_resolve_notifies_the_other_supervisors(self, db: AsyncSession, seed, client):
        p = await _people(db, seed)
        f1 = await _flag(db, seed, client, seed["record"])

        await _share(client, seed, [f1])
        # sharer isn't notified; the other DE supervisor and the customer are; the outsider isn't
        assert await _notices(db) == [(p["sup2"].id, "flag_shared", f1), (p["cust"].id, "flag_shared", f1)]

        resp = await client.post(f"/api/flags/{f1}/comments", json={"body": "Use the folio from the margin"}, headers=_cust(p["cust"]))
        assert resp.status_code == 201, resp.text
        assert resp.json()["author_side"] == "customer"
        resp = await client.post(f"/api/flags/{f1}/comments", json={"body": "Thanks, will do"}, headers=_de(p["sup2"]))
        assert resp.status_code == 201

        thread = (await client.get(f"/api/flags/{f1}/comments", headers=_cust(p["cust"]))).json()
        assert [(c["author_name"], c["author_side"], c["body"]) for c in thread] == [
            ("Customer Sup", "customer", "Use the folio from the margin"),
            ("Supervisor Two", "digitizing", "Thanks, will do"),
        ]

        await client.post(f"/api/flags/{f1}/resolve", json={"resolution": "Folio entered"}, headers=_de(p["sup"]))
        notices = await _notices(db)
        assert notices[2:] == [
            (p["sup"].id, "flag_comment", f1), (p["sup2"].id, "flag_comment", f1),   # customer's comment
            (p["sup"].id, "flag_comment", f1), (p["cust"].id, "flag_comment", f1),   # sup2's reply
            (p["sup2"].id, "flag_resolved", f1), (p["cust"].id, "flag_resolved", f1),
        ]
        assert all(uid != p["outsider"].id for uid, _, _ in notices)

        # the customer sees the outcome; the discussion is closed once resolved
        row = (await client.get(f"/api/flags/{f1}", headers=_cust(p["cust"]))).json()
        assert (row["status"], row["resolution"], row["comment_count"]) == ("resolved", "Folio entered", 2)
        resp = await client.post(f"/api/flags/{f1}/comments", json={"body": "one more"}, headers=_cust(p["cust"]))
        assert resp.status_code == 409

        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.entity_id == seed["record"].id).order_by(AuditLog.id)
        )).scalars().all()
        assert [a for a in actions if a in {
            AuditAction.flag_shared, AuditAction.flag_comment_added, AuditAction.flag_resolved,
        }] == [AuditAction.flag_shared, AuditAction.flag_comment_added, AuditAction.flag_comment_added, AuditAction.flag_resolved]

    async def test_cannot_discuss_an_unshared_flag(self, db: AsyncSession, seed, client):
        p = await _people(db, seed)
        f1 = await _flag(db, seed, client, seed["record"])
        resp = await client.post(f"/api/flags/{f1}/comments", json={"body": "hi"}, headers=_de(p["sup"]))
        assert resp.status_code == 409
        resp = await client.post(f"/api/flags/{f1}/comments", json={"body": "hi"}, headers=_cust(p["cust"]))
        assert resp.status_code == 404
        await _share(client, seed, [f1])
        resp = await client.post(f"/api/flags/{f1}/comments", json={"body": "  "}, headers=_cust(p["cust"]))
        assert resp.status_code == 422

    async def test_sharing_several_sends_one_summary(self, db: AsyncSession, seed, client):
        p = await _people(db, seed)
        f1 = await _flag(db, seed, client, seed["record"])
        f2 = await _flag(db, seed, client, seed["record2"], "Second")
        await _share(client, seed, [f1, f2])
        assert await _notices(db) == [(p["sup2"].id, "flag_shared", None), (p["cust"].id, "flag_shared", None)]
        await _share(client, seed, [f1, f2])  # already shared: nothing new
        assert len(await _notices(db)) == 2

    async def test_notification_endpoints_are_per_user(self, db: AsyncSession, seed, client):
        p = await _people(db, seed)
        f1 = await _flag(db, seed, client, seed["record"])
        await _share(client, seed, [f1])

        assert (await client.get("/api/notifications/unread-count", headers=_cust(p["cust"]))).json() == {"count": 1}
        notes = (await client.get("/api/notifications", headers=_cust(p["cust"]))).json()
        assert [(n["kind"], n["flag_id"], n["project_id"]) for n in notes] == [("flag_shared", f1, seed["project"].id)]
        assert "Supervisor shared the flag on" in notes[0]["message"]

        # marking someone else's id does nothing
        sup2_note = (await client.get("/api/notifications", headers=_de(p["sup2"]))).json()[0]["id"]
        resp = await client.post("/api/notifications/read", json={"ids": [sup2_note]}, headers=_cust(p["cust"]))
        assert resp.json() == {"marked": 0}
        assert (await client.get("/api/notifications/unread-count", headers=_de(p["sup2"]))).json() == {"count": 1}

        resp = await client.post("/api/notifications/read", json={}, headers=_cust(p["cust"]))
        assert resp.json() == {"marked": 1}
        assert (await client.get("/api/notifications/unread-count", headers=_cust(p["cust"]))).json() == {"count": 0}
        assert (await client.get("/api/notifications", params={"unread_only": True}, headers=_cust(p["cust"]))).json() == []


class TestCustomerRecordHistory:
    async def test_customer_history_shows_only_shared_flag_events(self, db: AsyncSession, seed, client):
        p = await _people(db, seed)
        f1 = await _flag(db, seed, client, seed["record"])
        await client.post(f"/api/flags/{f1}/resolve", json={"resolution": "internal fix"}, headers=_de(p["sup"]))
        f2 = await _flag(db, seed, client, seed["record"], "Needs the customer")
        await _share(client, seed, [f2])

        url = f"/api/records/{seed['record'].id}/history"
        cust_events = (await client.get(url, headers=_cust(p["cust"]))).json()
        flag_ids = [
            (e["new_value"] or {}).get("flag_id") or (e["old_value"] or {}).get("flag_id")
            for e in cust_events if "flag" in e["action"]
        ]
        assert flag_ids and set(flag_ids) == {f2}
        de_events = (await client.get(url, headers=_de(p["sup"]))).json()
        assert any(e["action"] == "flag_resolved" for e in de_events)


class TestExportIncludesSharing:
    async def test_csv_has_shared_and_discussion_columns(self, db: AsyncSession, seed, client):
        p = await _people(db, seed)
        f1 = await _flag(db, seed, client, seed["record"])
        await _share(client, seed, [f1])
        await client.post(f"/api/flags/{f1}/comments", json={"body": "Use the margin folio"}, headers=_cust(p["cust"]))
        resp = await client.get(f"/api/projects/{seed['project'].id}/flags/export", headers=_de(p["sup"]))
        row = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8")[1:])))[0]
        assert row["Shared by"] == "Supervisor"
        assert row["Shared with customer (UTC)"]
        assert "Customer Sup (customer): Use the margin folio" in row["Discussion"]



class TestSearch:
    """GET /projects/{id}/flags?q=: the customer's Flagged Records search."""

    async def test_search_matches_record_issue_and_discussion(self, db: AsyncSession, seed, client):
        p = await _people(db, seed)
        seed["record"].original_filename = "land_title_0017.png"
        seed["record2"].source_identifier = "CT-4471"
        await db.commit()
        f1 = await _flag(db, seed, client, seed["record"], "Owner name cut off")
        f2 = await _flag(db, seed, client, seed["record2"], "Folio 50% illegible")
        await _share(client, seed, [f1])

        async def ids(q, who):
            r = await client.get(_list_url(seed), params={"q": q}, headers=(_cust if who is p["cust"] else _de)(who))
            assert r.status_code == 200, r.text
            return sorted(x["id"] for x in r.json())

        cust, sup = p["cust"], p["sup"]
        assert await ids("OWNER", cust) == [f1]                 # issue, any case
        assert await ids("title_0017", cust) == [f1]            # record name; _ is literal
        assert await ids("title%0017", cust) == []              # % is literal, not a wildcard
        assert await ids(str(seed["record"].id), cust) == [f1]  # record id
        assert await ids("  ", cust) == [f1]                    # blank = no filter
        assert await ids("nothing like this", cust) == []
        # an unshared flag never turns up for the customer; the DE side finds it
        assert await ids("CT-4471", cust) == []
        assert await ids("ct-4471", sup) == [f2]
        assert await ids("50%", sup) == [f2]

        await client.post(f"/api/flags/{f1}/comments", json={"body": "See the Parish register"}, headers=_cust(cust))
        assert await ids("parish", cust) == [f1]                # discussion message
