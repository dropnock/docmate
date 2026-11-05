"""Record flags (flag_service, routers/flags.py): an indexer escalates an
issue on a record, a supervisor reviews and resolves it, and the record
stays in its batch throughout. Also the reopen rules start_task enforces
for an indexer going back to a finished record."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, AuditLog, BatchStatus, RecordFlag, RecordStatus, TaskStatus, TaskType
from app.services import batch_service, task_service
from tests.conftest import token


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {token(user)}", "X-Portal": "digitizing"}


async def _assign(db, seed, record, user=None, task_type=TaskType.indexing):
    task = await task_service.assign_task(
        db, record_id=record.id, batch_id=seed["batch"].id, task_type=task_type,
        agent_id=(user or seed["indexer"]).id, supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )
    await db.commit()
    return task


async def _index(db, seed, task, user=None):
    user = user or seed["indexer"]
    await task_service.start_task(db, task_id=task.id, user_id=user.id, tenant_id=seed["tenant"].id)
    await task_service.complete_task(
        db, task_id=task.id, user_id=user.id, tenant_id=seed["tenant"].id, indexed_data={"title": "x"},
    )
    await db.commit()


class TestRaisingFlags:
    async def test_indexer_flags_edits_and_withdraws(self, db: AsyncSession, seed, client):
        record = seed["record"]
        await _assign(db, seed, record)
        url = f"/api/records/{record.id}/flag"

        resp = await client.put(url, json={"reason": "Owner name is cut off"}, headers=_auth(seed["indexer"]))
        assert resp.status_code == 200, resp.text
        flag_id = resp.json()["id"]
        assert resp.json()["status"] == "open"

        resp = await client.put(url, json={"reason": "Owner name and folio cut off"}, headers=_auth(seed["indexer"]))
        assert resp.json()["id"] == flag_id  # still the one flag, note updated
        assert (await client.get(url, headers=_auth(seed["indexer"]))).json()["open"]["reason"] == "Owner name and folio cut off"

        tasks = (await client.get("/api/tasks/mine", headers=_auth(seed["indexer"]))).json()
        assert [t["flagged"] for t in tasks] == [True]

        resp = await client.delete(url, headers=_auth(seed["indexer"]))
        assert resp.json()["status"] == "withdrawn"
        assert (await client.get(url, headers=_auth(seed["indexer"]))).json()["open"] is None

        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.entity_id == record.id).order_by(AuditLog.id)
        )).scalars().all()
        assert actions.count(AuditAction.record_flagged) == 2
        assert AuditAction.flag_withdrawn in actions

    async def test_a_reason_is_required(self, db: AsyncSession, seed, client):
        await _assign(db, seed, seed["record"])
        resp = await client.put(f"/api/records/{seed['record'].id}/flag", json={"reason": "  "},
                                headers=_auth(seed["indexer"]))
        assert resp.status_code == 422

    async def test_only_the_assigned_indexer_or_a_supervisor_may_flag(self, db: AsyncSession, seed, client):
        await _assign(db, seed, seed["record"])
        url = f"/api/records/{seed['record'].id}/flag"
        resp = await client.put(url, json={"reason": "not mine"}, headers=_auth(seed["indexer2"]))
        assert resp.status_code == 403
        resp = await client.put(url, json={"reason": "supervisor note"}, headers=_auth(seed["supervisor"]))
        assert resp.status_code == 200

    async def test_only_the_raiser_or_a_supervisor_may_withdraw(self, db: AsyncSession, seed, client):
        await _assign(db, seed, seed["record"])
        await _assign(db, seed, seed["record"], user=seed["qa_staff"], task_type=TaskType.qa)
        url = f"/api/records/{seed['record'].id}/flag"
        await client.put(url, json={"reason": "smudged"}, headers=_auth(seed["indexer"]))
        assert (await client.delete(url, headers=_auth(seed["qa_staff"]))).status_code == 403
        assert (await client.delete(url, headers=_auth(seed["supervisor"]))).status_code == 200


class TestSupervisorReview:
    async def test_supervisor_lists_and_resolves_and_the_record_stays_put(self, db: AsyncSession, seed, client):
        record = seed["record"]
        task = await _assign(db, seed, record)
        await _index(db, seed, task)
        await client.put(f"/api/records/{record.id}/flag", json={"reason": "Two caveat numbers on the card"},
                         headers=_auth(seed["indexer"]))
        project_flags = f"/api/projects/{seed['project'].id}/flags"

        assert (await client.get(project_flags, headers=_auth(seed["indexer"]))).status_code == 403
        flags = (await client.get(project_flags, headers=_auth(seed["supervisor"]))).json()
        assert len(flags) == 1
        assert flags[0]["raised_by_name"] == seed["indexer"].full_name
        assert flags[0]["batch_name"] == seed["batch"].name

        resp = await client.post(f"/api/flags/{flags[0]['id']}/resolve", json={"resolution": "Use the stamped one"},
                                 headers=_auth(seed["supervisor"]))
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "resolved"
        assert (await client.get(project_flags, headers=_auth(seed["supervisor"]))).json() == []
        resolved = (await client.get(f"{project_flags}?status=resolved", headers=_auth(seed["supervisor"]))).json()
        assert resolved[0]["closed_by_name"] == seed["supervisor"].full_name

        seen = (await client.get(f"/api/records/{record.id}/flag", headers=_auth(seed["indexer"]))).json()
        assert seen["open"] is None and seen["last_resolved"]["resolution"] == "Use the stamped one"

        await db.refresh(record)
        assert record.batch_id == seed["batch"].id and record.status == RecordStatus.indexed

        again = await client.post(f"/api/flags/{flags[0]['id']}/resolve", json={"resolution": "again"},
                                  headers=_auth(seed["supervisor"]))
        assert again.status_code == 409

    async def test_open_flag_holds_the_batch_in_indexing(self, db: AsyncSession, seed, client):
        for record in (seed["record"], seed["record2"]):
            await _index(db, seed, await _assign(db, seed, record))
        await client.put(f"/api/records/{seed['record'].id}/flag", json={"reason": "check"},
                         headers=_auth(seed["indexer"]))

        with pytest.raises(HTTPException) as exc:
            await batch_service.complete_indexing_batch(
                db, batch_id=seed["batch"].id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 400 and "flagged" in exc.value.detail

        flag = (await db.execute(select(RecordFlag))).scalar_one()
        await client.post(f"/api/flags/{flag.id}/resolve", json={"resolution": "fine as is"},
                          headers=_auth(seed["supervisor"]))
        batch = await batch_service.complete_indexing_batch(
            db, batch_id=seed["batch"].id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id,
        )
        assert batch.status == BatchStatus.qa_review


class TestReopening:
    async def test_indexer_can_reopen_a_finished_record_while_the_batch_is_open(self, db: AsyncSession, seed):
        task = await _assign(db, seed, seed["record"])
        await _index(db, seed, task)
        reopened = await task_service.start_task(
            db, task_id=task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id,
        )
        assert reopened.status == TaskStatus.in_progress
        assert seed["record"].status == RecordStatus.indexing

    async def test_no_reopening_once_the_batch_has_moved_on(self, db: AsyncSession, seed):
        task = await _assign(db, seed, seed["record"])
        await _index(db, seed, task)
        seed["batch"].status = BatchStatus.qa_review
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await task_service.start_task(db, task_id=task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id)
        assert exc.value.status_code == 409
