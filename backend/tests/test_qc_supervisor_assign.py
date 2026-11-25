"""Customer supervisors and QC batches: they can take batches themselves or
give them to other supervisors, see every batch with who holds it, reassign
it, and hand out only as many batches as they choose."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, OrgType
from app.models.aql import SamplingMode
from app.models.audit_log import AuditAction, AuditLog
from app.models.batch import Batch
from app.models.task import Task, TaskStatus, TaskType
from app.services import analytics_service, lot_service, task_service
from tests.conftest import token
from tests.test_lot_inspections import _make_customer_supervisor, _make_lot
from tests.test_lot_resubmit import ACCEPT, _inspect_and_release

CUST = "customer"


def _h(user, portal=CUST):
    return {"Authorization": f"Bearer {token(user, portal_override=portal)}", "X-Portal": portal}


async def _sampled_lot(db, seed, n=6):
    """A released lot, fully sampled, ready for QC batches."""
    lot, records = await _make_lot(db, seed, num_records=n)
    await _inspect_and_release(db, seed, lot)
    seed["aql_config"].sampling_mode = SamplingMode.manual
    await lot_service.apply_sample(
        db, lot_id=lot.id, sample_rate=1.0, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )
    doc_type_id = (await db.get(Batch, records[0].batch_id)).document_type_id
    await db.commit()
    return lot, sorted(r.id for r in records), doc_type_id


async def _cust_sup(db, seed, sub="sub-cust-sup"):
    sup = await _make_customer_supervisor(db, seed["cust_org"].id, seed, sub=sub)
    sup.full_name = f"Supervisor {sub}"
    await db.commit()
    return sup


def _body(lot, doc_type_id, assignments):
    return {"project_id": lot.project_id, "document_type_id": doc_type_id,
            "assignments": [{"agent_id": a, "record_ids": r} for a, r in assignments]}


class TestAssignees:
    async def test_supervisor_can_assign_to_self_and_other_supervisor(self, db: AsyncSession, seed, client):
        lot, ids, doc = await _sampled_lot(db, seed)
        me = await _cust_sup(db, seed)
        other = await _cust_sup(db, seed, sub="sub-cust-sup-2")
        resp = await client.post(f"/api/lots/{lot.id}/qc-batches", headers=_h(me), json=_body(
            lot, doc, [(me.id, ids[:2]), (other.id, ids[2:4]), (seed["qc_agent"].id, ids[4:])],
        ))
        assert resp.status_code == 201, resp.text
        holders = dict((await db.execute(
            select(Task.record_id, Task.assigned_to).where(Task.task_type == TaskType.qc)
        )).all())
        assert holders == {**{r: me.id for r in ids[:2]}, **{r: other.id for r in ids[2:4]},
                           **{r: seed["qc_agent"].id for r in ids[4:]}}

    async def test_supervisor_works_their_own_qc_task(self, db: AsyncSession, seed, client):
        lot, ids, doc = await _sampled_lot(db, seed, n=2)
        me = await _cust_sup(db, seed)
        await client.post(f"/api/lots/{lot.id}/qc-batches", headers=_h(me), json=_body(lot, doc, [(me.id, ids)]))
        mine = (await client.get("/api/tasks/mine", headers=_h(me))).json()
        assert sorted(t["record_id"] for t in mine) == ids
        task_id = mine[0]["id"]
        assert (await client.post(f"/api/tasks/{task_id}/start", headers=_h(me))).status_code == 200
        # SQLite hands timestamps back without their zone; put it back.
        task = await db.get(Task, task_id)
        task.started_at = datetime.now(timezone.utc)
        done = await client.post(f"/api/tasks/{task_id}/complete", headers=_h(me), json={
            "field_results": [fr.model_dump() for fr in ACCEPT],
        })
        assert done.status_code == 200, done.text

    async def test_people_outside_the_customer_cannot_be_given_qc_batches(self, db: AsyncSession, seed, client):
        lot, ids, doc = await _sampled_lot(db, seed, n=2)
        me = await _cust_sup(db, seed)
        other_org = Organization(tenant_id=seed["tenant"].id, name="Other", type=OrgType.customer)
        db.add(other_org)
        await db.flush()
        foreign = await _make_customer_supervisor(db, other_org.id, seed, sub="sub-foreign")
        await db.commit()
        for outsider in (foreign, seed["supervisor"], seed["indexer"]):
            resp = await client.post(f"/api/lots/{lot.id}/qc-batches", headers=_h(me),
                                     json=_body(lot, doc, [(outsider.id, ids)]))
            assert resp.status_code == 422, (outsider.email, resp.text)


class TestPartialAssignment:
    async def test_assign_some_batches_now_and_the_rest_later(self, db: AsyncSession, seed, client):
        lot, ids, doc = await _sampled_lot(db, seed, n=6)
        me = await _cust_sup(db, seed)
        first = await client.post(f"/api/lots/{lot.id}/qc-batches", headers=_h(me),
                                  json=_body(lot, doc, [(seed["qc_agent"].id, ids[:2])]))
        assert first.status_code == 201

        detail = (await client.get(f"/api/lots/{lot.id}", headers=_h(me))).json()
        by_id = {r["record_id"]: r for r in detail["records"]}
        assert {r for r, v in by_id.items() if v["qc_task"]} == set(ids[:2])
        assert by_id[ids[0]]["qc_task"]["assigned_to_name"] == seed["qc_agent"].full_name
        assert by_id[ids[0]]["qc_task"]["status"] == "pending"

        again = await client.post(f"/api/lots/{lot.id}/qc-batches", headers=_h(me),
                                  json=_body(lot, doc, [(me.id, ids[:1])]))
        assert again.status_code == 400  # already assigned
        later = await client.post(f"/api/lots/{lot.id}/qc-batches", headers=_h(me),
                                  json=_body(lot, doc, [(me.id, ids[2:4]), (me.id, ids[4:])]))
        assert later.status_code == 201 and len(later.json()) == 2


class TestBatchList:
    async def test_lists_batches_with_holders_and_progress(self, db: AsyncSession, seed, client):
        lot, ids, doc = await _sampled_lot(db, seed, n=4)
        other_lot, other_ids, other_doc = await _sampled_lot(db, seed, n=2)
        me = await _cust_sup(db, seed)
        await client.post(f"/api/lots/{lot.id}/qc-batches", headers=_h(me),
                          json=_body(lot, doc, [(seed["qc_agent"].id, ids[:3]), (me.id, ids[3:])]))
        await client.post(f"/api/lots/{other_lot.id}/qc-batches", headers=_h(me),
                          json=_body(other_lot, other_doc, [(me.id, other_ids)]))
        # The agent finishes one record.
        task = (await db.execute(select(Task).where(Task.record_id == ids[0], Task.task_type == TaskType.qc))).scalar_one()
        task.status, task.started_at = TaskStatus.in_progress, datetime.now(timezone.utc)
        await db.flush()
        await task_service.complete_task(db, task_id=task.id, user_id=seed["qc_agent"].id,
                                         tenant_id=seed["tenant"].id, field_results=ACCEPT)
        await db.commit()

        resp = await client.get(f"/api/lots/project/{lot.project_id}/qc-batches", headers=_h(me))
        assert resp.status_code == 200
        rows = resp.json()
        assert [r["lot_id"] for r in rows] == [other_lot.id, lot.id, lot.id]  # newest first
        agent_batch = next(r for r in rows if r["total"] == 3)
        assert (agent_batch["completed"], agent_batch["pending"], agent_batch["lot_name"]) == (1, 2, lot.name)
        assert agent_batch["assignees"] == [{
            "id": seed["qc_agent"].id, "full_name": seed["qc_agent"].full_name,
            "role": "customer_qc_agent", "tasks": 3, "open": 2,
        }]

        one_lot = (await client.get(f"/api/lots/project/{lot.project_id}/qc-batches",
                                    params={"lot_id": lot.id}, headers=_h(me))).json()
        assert {r["lot_id"] for r in one_lot} == {lot.id} and len(one_lot) == 2

    async def test_only_customer_supervisors_see_it(self, db: AsyncSession, seed, client):
        lot, _, _ = await _sampled_lot(db, seed, n=1)
        url = f"/api/lots/project/{lot.project_id}/qc-batches"
        assert (await client.get(url, headers=_h(seed["qc_agent"]))).status_code == 403
        assert (await client.get(url, headers=_h(seed["supervisor"], "digitizing"))).status_code == 403


class TestReassign:
    async def _setup(self, db, seed, client):
        lot, ids, doc = await _sampled_lot(db, seed, n=3)
        me = await _cust_sup(db, seed)
        resp = await client.post(f"/api/lots/{lot.id}/qc-batches", headers=_h(me),
                                 json=_body(lot, doc, [(seed["qc_agent"].id, ids)]))
        batch_id = resp.json()[0]["id"]
        # One record done, one open on screen (locked), one waiting.
        tasks = (await db.execute(select(Task).where(Task.batch_id == batch_id).order_by(Task.id))).scalars().all()
        tasks[0].status, tasks[0].started_at = TaskStatus.in_progress, datetime.now(timezone.utc)
        await db.flush()
        await task_service.complete_task(db, task_id=tasks[0].id, user_id=seed["qc_agent"].id,
                                         tenant_id=seed["tenant"].id, field_results=ACCEPT)
        await task_service.start_task(db, task_id=tasks[1].id, user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id)
        await db.commit()
        return lot, batch_id, tasks, me

    async def test_moves_open_tasks_to_a_supervisor_and_keeps_finished_ones(self, db: AsyncSession, seed, client):
        lot, batch_id, tasks, me = await self._setup(db, seed, client)
        resp = await client.post(f"/api/batches/{batch_id}/reassign", headers=_h(me),
                                 json={"task_type": "qc", "agent_id": me.id})
        assert resp.status_code == 200, resp.text
        assert sorted(t["id"] for t in resp.json()) == [tasks[1].id, tasks[2].id]
        for t in tasks:
            await db.refresh(t)
        assert tasks[0].assigned_to == seed["qc_agent"].id and tasks[0].status == TaskStatus.completed
        assert all(t.assigned_to == me.id and t.status == TaskStatus.pending for t in tasks[1:])
        from app.models.record import Record
        assert (await db.get(Record, tasks[1].record_id)).locked_by is None  # the agent's lock let go
        events = (await db.execute(select(AuditLog).where(AuditLog.action == AuditAction.reassigned))).scalars().all()
        assert sorted(e.entity_id for e in events) == [tasks[1].id, tasks[2].id]

        rows = (await client.get(f"/api/lots/project/{lot.project_id}/qc-batches", headers=_h(me))).json()
        assert [(a["full_name"], a["open"]) for a in rows[0]["assignees"]] == [
            (me.full_name, 2), (seed["qc_agent"].full_name, 0),
        ]

    async def test_reassign_guards(self, db: AsyncSession, seed, client):
        _, batch_id, tasks, me = await self._setup(db, seed, client)
        url = f"/api/batches/{batch_id}/reassign"
        # Not to someone outside the customer.
        resp = await client.post(url, headers=_h(me), json={"task_type": "qc", "agent_id": seed["indexer"].id})
        assert resp.status_code == 422
        # The digitizing supervisor doesn't move customer QC work, nor the customer DE work.
        resp = await client.post(url, headers=_h(seed["supervisor"], "digitizing"),
                                 json={"task_type": "qc", "agent_id": me.id})
        assert resp.status_code == 403
        resp = await client.post(url, headers=_h(me), json={"task_type": "qa", "agent_id": me.id})
        assert resp.status_code == 403
        # A finished task can't be reassigned (it would reopen the work).
        resp = await client.patch(f"/api/tasks/{tasks[0].id}/reassign", params={"agent_id": me.id}, headers=_h(me))
        assert resp.status_code == 409


async def test_staff_productivity_lists_supervisors_doing_qc(db: AsyncSession, seed):
    lot, ids, doc = await _sampled_lot(db, seed, n=2)
    working = await _cust_sup(db, seed)
    idle = await _cust_sup(db, seed, sub="sub-cust-sup-idle")
    await lot_service.create_qc_batches(
        db, lot_id=lot.id, project_id=lot.project_id, document_type_id=doc,
        assignments=[{"agent_id": working.id, "record_ids": ids}], supervisor_id=working.id,
        tenant_id=seed["tenant"].id,
    )
    await db.flush()
    rows = await analytics_service.qc_staff_productivity(db, project_id=lot.project_id)
    names = {r["full_name"] for r in rows}
    assert working.full_name in names and seed["qc_agent"].full_name in names
    assert idle.full_name not in names
