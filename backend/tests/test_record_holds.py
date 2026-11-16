"""Record holds (hold_service, routers/holds.py): a supervisor holds an
unassigned record so it isn't put into a batch, lists held records, and
cancels the hold."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, AuditLog, Cabinet, Record, RecordStatus, TaskType
from app.services import task_service
from tests.conftest import token


def _auth(user, portal="digitizing") -> dict:
    return {"Authorization": f"Bearer {token(user)}", "X-Portal": portal}


async def _cabinet(db: AsyncSession, seed, n: int = 3) -> tuple[Cabinet, list[Record]]:
    cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Intake")
    db.add(cabinet)
    await db.flush()
    records = [Record(cabinet_id=cabinet.id, status=RecordStatus.pending, original_filename=f"r{i}.pdf") for i in range(n)]
    db.add_all(records)
    await db.commit()
    return cabinet, records


def _url(seed, suffix: str = "") -> str:
    return f"/api/projects/{seed['project'].id}/holds{suffix}"


class TestHolding:
    async def test_hold_list_and_cancel(self, db: AsyncSession, seed, client):
        _, (r1, r2, _r3) = await _cabinet(db, seed)
        sup = _auth(seed["supervisor"])

        resp = await client.post(_url(seed), json={"record_ids": [r1.id, r2.id], "reason": "Waiting on customer"}, headers=sup)
        assert resp.status_code == 201, resp.text
        assert {h["record_id"] for h in resp.json()} == {r1.id, r2.id}

        held = (await client.get(_url(seed), headers=sup)).json()
        assert {h["record_id"] for h in held} == {r1.id, r2.id}
        assert held[0]["held_by_name"] == "Supervisor"
        assert held[0]["reason"] == "Waiting on customer"

        resp = await client.post(_url(seed, "/release"), json={"record_ids": [r1.id], "note": "Customer replied"}, headers=sup)
        assert resp.status_code == 200, resp.text
        assert [h["record_id"] for h in (await client.get(_url(seed), headers=sup)).json()] == [r2.id]
        cancelled = (await client.get(_url(seed), params={"active": False}, headers=sup)).json()
        assert [(h["record_id"], h["release_note"], h["released_by_name"]) for h in cancelled] == [
            (r1.id, "Customer replied", "Supervisor"),
        ]

        # held again after cancelling — a new hold row, history kept
        resp = await client.post(_url(seed), json={"record_ids": [r1.id], "reason": "Again"}, headers=sup)
        assert resp.status_code == 201

        events = (await db.execute(
            select(AuditLog.action, AuditLog.entity_id).where(
                AuditLog.action.in_([AuditAction.record_held, AuditAction.hold_released])
            ).order_by(AuditLog.id)
        )).all()
        assert events == [
            (AuditAction.record_held, r1.id), (AuditAction.record_held, r2.id),
            (AuditAction.hold_released, r1.id), (AuditAction.record_held, r1.id),
        ]
        assert (await db.get(Record, r1.id)).status == RecordStatus.pending  # holding never moves the record

    async def test_a_reason_is_required(self, db: AsyncSession, seed, client):
        _, (r1, *_) = await _cabinet(db, seed)
        resp = await client.post(_url(seed), json={"record_ids": [r1.id], "reason": "  "}, headers=_auth(seed["supervisor"]))
        assert resp.status_code == 422

    async def test_only_unassigned_records_can_be_held(self, db: AsyncSession, seed, client):
        _, (r1, r2, _r3) = await _cabinet(db, seed)
        r2.batch_id = seed["batch"].id
        await db.commit()
        resp = await client.post(_url(seed), json={"record_ids": [r1.id, r2.id], "reason": "x"}, headers=_auth(seed["supervisor"]))
        assert resp.status_code == 409
        assert str(r2.id) in resp.json()["detail"]
        assert (await client.get(_url(seed), headers=_auth(seed["supervisor"]))).json() == []  # all or nothing

    async def test_cannot_hold_twice_or_cancel_what_isnt_held(self, db: AsyncSession, seed, client):
        _, (r1, r2, _r3) = await _cabinet(db, seed)
        sup = _auth(seed["supervisor"])
        await client.post(_url(seed), json={"record_ids": [r1.id], "reason": "x"}, headers=sup)
        assert (await client.post(_url(seed), json={"record_ids": [r1.id], "reason": "y"}, headers=sup)).status_code == 409
        assert (await client.post(_url(seed, "/release"), json={"record_ids": [r2.id]}, headers=sup)).status_code == 404

    async def test_record_from_another_project_is_not_found(self, db: AsyncSession, seed, client):
        _, (r1, *_) = await _cabinet(db, seed)
        orphan = Record(status=RecordStatus.pending)  # no cabinet → not in this project
        db.add(orphan)
        await db.commit()
        resp = await client.post(_url(seed), json={"record_ids": [orphan.id], "reason": "x"}, headers=_auth(seed["supervisor"]))
        assert resp.status_code == 404

    async def test_supervisors_only(self, db: AsyncSession, seed, client):
        _, (r1, *_) = await _cabinet(db, seed)
        resp = await client.post(_url(seed), json={"record_ids": [r1.id], "reason": "x"}, headers=_auth(seed["indexer"]))
        assert resp.status_code == 403
        assert (await client.get(_url(seed), headers=_auth(seed["qc_agent"], "customer"))).status_code == 403


class TestHeldRecordsAreNotAssigned:
    async def test_batch_creation_refuses_held_records(self, db: AsyncSession, seed, client):
        cabinet, (r1, r2, _r3) = await _cabinet(db, seed)
        sup = _auth(seed["supervisor"])
        await client.post(_url(seed), json={"record_ids": [r1.id], "reason": "x"}, headers=sup)

        body = {
            "project_id": seed["project"].id, "document_type_id": seed["doc_type"].id,
            "record_ids": [r1.id, r2.id], "agent_id": seed["indexer"].id,
        }
        resp = await client.post(f"/api/cabinets/{cabinet.id}/batches", json=body, headers=sup)
        assert resp.status_code == 409
        assert str(r1.id) in resp.json()["detail"]
        assert (await db.get(Record, r2.id)).batch_id is None  # nothing assigned

        await client.post(_url(seed, "/release"), json={"record_ids": [r1.id]}, headers=sup)
        resp = await client.post(f"/api/cabinets/{cabinet.id}/batches", json=body, headers=sup)
        assert resp.status_code == 201, resp.text

    async def test_assignment_picker_leaves_held_records_out(self, db: AsyncSession, seed, client):
        cabinet, (r1, r2, r3) = await _cabinet(db, seed)
        sup = _auth(seed["supervisor"])
        await client.post(_url(seed), json={"record_ids": [r2.id], "reason": "x"}, headers=sup)

        url = f"/api/cabinets/{cabinet.id}/records"
        picker = (await client.get(url, params={"status": "pending", "exclude_held": True}, headers=sup)).json()
        assert [r["id"] for r in picker] == [r1.id, r3.id]
        everything = (await client.get(url, params={"status": "pending"}, headers=sup)).json()
        assert {r["id"]: r["on_hold"] for r in everything} == {r1.id: False, r2.id: True, r3.id: False}

    async def test_single_task_assignment_refuses_held_record(self, db: AsyncSession, seed, client):
        _, (r1, *_) = await _cabinet(db, seed)
        await client.post(_url(seed), json={"record_ids": [r1.id], "reason": "x"}, headers=_auth(seed["supervisor"]))
        with pytest.raises(HTTPException) as exc:
            await task_service.assign_task(
                db, record_id=r1.id, batch_id=seed["batch"].id, task_type=TaskType.indexing,
                agent_id=seed["indexer"].id, supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 409
