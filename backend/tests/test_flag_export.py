"""Flag list CSV export (GET /projects/{id}/flags/export)."""
import csv
import io

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, AuditEntityType, AuditLog, TaskType
from app.services import task_service
from tests.conftest import token


def _auth(user, portal="digitizing") -> dict:
    return {"Authorization": f"Bearer {token(user)}", "X-Portal": portal}


async def _assign(db, seed, record):
    await task_service.assign_task(
        db, record_id=record.id, batch_id=seed["batch"].id, task_type=TaskType.indexing,
        agent_id=seed["indexer"].id, supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )
    await db.commit()


def _rows(resp) -> list[dict]:
    text = resp.content.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.DictReader(io.StringIO(text[1:])))


class TestFlagExport:
    async def test_exports_the_current_tab_as_csv_and_audits_it(self, db: AsyncSession, seed, client):
        r1, r2 = seed["record"], seed["record2"]
        for r in (r1, r2):
            await _assign(db, seed, r)
        await client.put(f"/api/records/{r1.id}/flag", json={"reason": "Folio, illegible"}, headers=_auth(seed["indexer"]))
        await client.put(f"/api/records/{r2.id}/flag", json={"reason": "=HYPERLINK(\"http://evil\")"}, headers=_auth(seed["indexer"]))
        flag2 = (await client.get(f"/api/records/{r2.id}/flag", headers=_auth(seed["indexer"]))).json()["open"]["id"]
        await client.post(f"/api/flags/{flag2}/resolve", json={"resolution": "Re-scanned"}, headers=_auth(seed["supervisor"]))

        url = f"/api/projects/{seed['project'].id}/flags/export"
        resp = await client.get(url, params={"status": "open"}, headers=_auth(seed["supervisor"]))
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        rows = _rows(resp)
        assert [(r["Record ID"], r["Issue"], r["Flag status"], r["Flagged by"], r["Batch"]) for r in rows] == [
            (str(r1.id), "Folio, illegible", "open", "Indexer One", "Batch 001"),
        ]

        rows = _rows(await client.get(url, params={"status": "resolved"}, headers=_auth(seed["supervisor"])))
        assert rows[0]["Issue"] == "'=HYPERLINK(\"http://evil\")"  # not run as a formula
        assert rows[0]["Resolution"] == "Re-scanned"
        assert rows[0]["Closed by"] == "Supervisor"

        assert len(_rows(await client.get(url, params={"status": ""}, headers=_auth(seed["supervisor"])))) == 2

        events = (await db.execute(
            select(AuditLog).where(AuditLog.action == AuditAction.flags_exported).order_by(AuditLog.id)
        )).scalars().all()
        assert [(e.entity_type, e.entity_id, e.new_value) for e in events] == [
            (AuditEntityType.project, seed["project"].id, {"status": "open", "rows": 1}),
            (AuditEntityType.project, seed["project"].id, {"status": "resolved", "rows": 1}),
            (AuditEntityType.project, seed["project"].id, {"status": "all", "rows": 2}),
        ]

    async def test_supervisors_only(self, db: AsyncSession, seed, client):
        url = f"/api/projects/{seed['project'].id}/flags/export"
        assert (await client.get(url, headers=_auth(seed["indexer"]))).status_code == 403
        assert (await client.get(url, params={"status": "bogus"}, headers=_auth(seed["supervisor"]))).status_code == 422
