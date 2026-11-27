"""Who's online (services/presence_service.py, GET /api/users/online)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus, TaskType
from app.services import presence_service
from tests.conftest import token

T0 = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
DE = {"X-Portal": "digitizing"}


def auth(user, portal="digitizing"):
    return {"Authorization": f"Bearer {token(user)}", "X-Portal": portal}


class TestTouch:
    async def test_first_request_starts_a_session(self, db: AsyncSession, seed):
        u = seed["indexer"]
        await presence_service.touch(db, u, T0)
        await db.flush()
        await db.refresh(u)
        assert presence_service._utc(u.last_seen_at) == T0
        assert presence_service._utc(u.session_started_at) == T0

    async def test_throttled_within_a_minute(self, db: AsyncSession, seed):
        u = seed["indexer"]
        await presence_service.touch(db, u, T0)
        await presence_service.touch(db, u, T0 + timedelta(seconds=30))
        await db.flush()
        await db.refresh(u)
        assert presence_service._utc(u.last_seen_at) == T0

    async def test_session_continues_then_restarts_after_gap(self, db: AsyncSession, seed):
        u = seed["indexer"]
        await presence_service.touch(db, u, T0)
        await presence_service.touch(db, u, T0 + timedelta(minutes=10))
        await db.flush()
        await db.refresh(u)
        assert presence_service._utc(u.session_started_at) == T0
        later = T0 + timedelta(minutes=55)
        await presence_service.touch(db, u, later)
        await db.flush()
        await db.refresh(u)
        assert presence_service._utc(u.session_started_at) == later

    async def test_does_not_bump_updated_at(self, db: AsyncSession, seed):
        u = seed["indexer"]
        before = u.updated_at
        await presence_service.touch(db, u, T0)
        await db.flush()
        await db.refresh(u)
        assert u.updated_at == before


class TestOnline:
    async def test_lists_recent_users_with_open_task(self, db: AsyncSession, seed):
        now = T0 + timedelta(minutes=20)
        await presence_service.touch(db, seed["indexer"], now - timedelta(seconds=30))
        await presence_service.touch(db, seed["qc_agent"], now - timedelta(minutes=10))
        await presence_service.touch(db, seed["indexer2"], now - timedelta(hours=2))
        db.add(Task(
            record_id=seed["record"].id, batch_id=seed["batch"].id, task_type=TaskType.indexing,
            assigned_to=seed["indexer"].id, status=TaskStatus.in_progress,
            started_at=now - timedelta(minutes=5), last_active_at=now - timedelta(seconds=20),
        ))
        await db.flush()

        rows = await presence_service.online_users(
            db, tenant_id=seed["tenant"].id, within=timedelta(minutes=15), now=now
        )
        assert [r["full_name"] for r in rows] == ["Indexer One", "QC Agent"]
        ix, qc = rows
        assert ix["status"] == "active"
        assert ix["task"]["working"] is True
        assert ix["task"]["batch_name"] == "Batch 001"
        assert ix["task"]["project_name"] == "Test Project"
        assert qc["status"] == "idle"
        assert qc["task"] is None
        assert qc["organization_name"] == "Cust Org"

    async def test_endpoint_admin_only_and_stamps_caller(self, client, seed):
        r = await client.get("/api/users/online", headers=auth(seed["supervisor"]))
        assert r.status_code == 403
        r = await client.get("/api/users/online", headers=auth(seed["admin"]))
        assert r.status_code == 200
        names = [u["full_name"] for u in r.json()]
        # Both requests stamped their callers.
        assert "Admin" in names and "Supervisor" in names
        assert "Indexer One" not in names

    async def test_customer_request_counts(self, client, seed):
        r = await client.get("/api/users/me", headers=auth(seed["qc_agent"], "customer"))
        assert r.status_code == 200
        r = await client.get("/api/users/online", headers=auth(seed["admin"]))
        assert any(u["full_name"] == "QC Agent" and u["portal"] == "customer" for u in r.json())
