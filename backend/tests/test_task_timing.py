"""Time on a record added up across sessions (services/task_timing.py) and
the per-record detail behind the productivity reports' Avg Time."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus, TaskType
from app.services import analytics_service, task_service, task_timing
from tests.conftest import token

T0 = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _task(**kw) -> Task:
    kw.setdefault("status", TaskStatus.pending)
    t = Task(record_id=1, batch_id=1, task_type=TaskType.indexing, **kw)
    t.active_seconds = kw.get("active_seconds", 0)
    t.sessions = kw.get("sessions", 0)
    return t


class TestTiming:
    def test_heartbeats_add_up(self):
        t = _task()
        task_timing.begin_session(t, at(0))
        t.status = TaskStatus.in_progress
        for s in (30, 60, 90):
            task_timing.heartbeat(t, at(s))
        task_timing.end_session(t, at(100))
        assert t.processing_time_seconds == 100
        assert t.sessions == 1
        assert t.started_at == at(0)

    def test_time_away_is_not_counted(self):
        t = _task()
        task_timing.begin_session(t, at(0))
        t.status = TaskStatus.in_progress
        task_timing.heartbeat(t, at(30))
        task_timing.heartbeat(t, at(60))
        # Leaves for an hour, comes back and opens the record again.
        task_timing.begin_session(t, at(3660))
        task_timing.heartbeat(t, at(3690))
        task_timing.end_session(t, at(3700))
        assert t.processing_time_seconds == 60 + 40
        assert t.sessions == 2
        assert t.started_at == at(0)

    def test_idle_gap_inside_a_session_is_not_counted(self):
        t = _task()
        task_timing.begin_session(t, at(0))
        t.status = TaskStatus.in_progress
        task_timing.heartbeat(t, at(30))
        task_timing.heartbeat(t, at(630))  # idle for 10 minutes, then back
        task_timing.end_session(t, at(650))
        assert t.processing_time_seconds == 30 + 20

    def test_reload_continues_the_session(self):
        t = _task()
        task_timing.begin_session(t, at(0))
        t.status = TaskStatus.in_progress
        task_timing.heartbeat(t, at(30))
        task_timing.begin_session(t, at(40))
        task_timing.end_session(t, at(50))
        assert t.sessions == 1
        assert t.processing_time_seconds == 50

    def test_reopening_a_finished_task_adds_to_its_time(self):
        t = _task()
        task_timing.begin_session(t, at(0))
        t.status = TaskStatus.in_progress
        task_timing.end_session(t, at(60))
        t.status = TaskStatus.completed
        task_timing.begin_session(t, at(600))
        t.status = TaskStatus.in_progress
        task_timing.end_session(t, at(620))
        assert t.processing_time_seconds == 80
        assert t.sessions == 2

    def test_legacy_task_keeps_old_measure(self):
        t = _task(status=TaskStatus.in_progress, started_at=at(0))
        task_timing.end_session(t, at(300))
        assert t.processing_time_seconds == 300

    def test_legacy_finished_task_reopened_keeps_its_time(self):
        t = _task(status=TaskStatus.completed, started_at=at(0), processing_time_seconds=300)
        task_timing.begin_session(t, at(1000))
        t.status = TaskStatus.in_progress
        task_timing.end_session(t, at(1030))
        assert t.processing_time_seconds == 330

    def test_reset_on_reassign(self):
        t = _task()
        task_timing.begin_session(t, at(0))
        task_timing.heartbeat(t, at(30))
        task_timing.reset(t)
        assert (t.active_seconds, t.sessions, t.started_at, t.last_active_at) == (0, 0, None, None)


async def _started(db, seed):
    task = await task_service.assign_task(
        db, record_id=seed["record"].id, batch_id=seed["batch"].id,
        task_type=TaskType.indexing, agent_id=seed["indexer"].id,
        supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )
    await db.flush()
    await task_service.start_task(db, task_id=task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id)
    await db.flush()
    return task


class TestHeartbeatEndpoint:
    async def test_heartbeat_counts_time(self, db: AsyncSession, seed, client):
        task = await _started(db, seed)
        assert task.sessions == 1
        task.last_active_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        await db.commit()
        headers = {"Authorization": f"Bearer {token(seed['indexer'])}", "X-Portal": "digitizing"}
        r = await client.post(f"/api/tasks/{task.id}/heartbeat", headers=headers)
        assert r.status_code == 204
        await db.flush()  # the test client shares this session
        await db.refresh(task)
        assert 29 <= task.active_seconds <= 32

    async def test_heartbeat_rejects_other_users_and_closed_tasks(self, db: AsyncSession, seed, client):
        task = await _started(db, seed)
        await db.commit()
        other = {"Authorization": f"Bearer {token(seed['indexer2'])}", "X-Portal": "digitizing"}
        assert (await client.post(f"/api/tasks/{task.id}/heartbeat", headers=other)).status_code == 403
        task.status = TaskStatus.completed
        await db.commit()
        mine = {"Authorization": f"Bearer {token(seed['indexer'])}", "X-Portal": "digitizing"}
        assert (await client.post(f"/api/tasks/{task.id}/heartbeat", headers=mine)).status_code == 409

    async def test_reassign_starts_the_new_agent_from_nothing(self, db: AsyncSession, seed):
        task = await _started(db, seed)
        task.active_seconds = 120
        await task_service.reassign_task(
            db, task_id=task.id, new_agent_id=seed["indexer2"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert (task.active_seconds, task.sessions, task.started_at) == (0, 0, None)


def _done(seed, *, seconds, sessions=1, completed_at, status=TaskStatus.completed, task_type=TaskType.indexing,
          user="indexer", record="record"):
    return Task(
        record_id=seed[record].id, batch_id=seed["batch"].id, task_type=task_type,
        assigned_to=seed[user].id, status=status, started_at=completed_at - timedelta(hours=1),
        completed_at=completed_at, processing_time_seconds=seconds, active_seconds=seconds, sessions=sessions,
    )


class TestStaffTaskTimes:
    async def test_rows_and_summary(self, db: AsyncSession, seed):
        now = datetime.now(timezone.utc)
        db.add_all([
            _done(seed, seconds=60, completed_at=now),
            _done(seed, seconds=200, sessions=3, completed_at=now, record="record2"),
            _done(seed, seconds=100, completed_at=now, status=TaskStatus.failed),
            _done(seed, seconds=999, completed_at=now, user="indexer2"),            # someone else
            _done(seed, seconds=999, completed_at=now, task_type=TaskType.qa),      # other task type
            _done(seed, seconds=500, completed_at=now - timedelta(days=3)),         # outside the day
        ])
        await db.commit()
        out = await analytics_service.staff_task_times(
            db, project_id=seed["project"].id, user_id=seed["indexer"].id, task_type=TaskType.indexing,
            date_from=now.date(), date_to=now.date(),
        )
        assert [r["seconds"] for r in out["rows"]] == [200, 100, 60]
        assert out["summary"] == {
            "tasks": 3, "finished": 3, "total_seconds": 360, "avg_seconds": 120, "median_seconds": 100,
            "fastest_seconds": 60, "slowest_seconds": 200, "reopened": 1,
        }
        assert [r["outcome"] for r in out["rows"]] == ["submitted", "requeued", "submitted"]
        assert out["rows"][0]["sessions"] == 3

        everything = await analytics_service.staff_task_times(
            db, project_id=seed["project"].id, user_id=seed["indexer"].id, task_type=TaskType.indexing,
            sort="time_asc", limit=2,
        )
        assert everything["total"] == 4
        assert [r["seconds"] for r in everything["rows"]] == [60, 100]

    async def test_in_progress_shows_time_so_far(self, db: AsyncSession, seed):
        task = await _started(db, seed)
        task.active_seconds = 45
        task.processing_time_seconds = 10  # from an earlier finish, since reopened
        await db.commit()
        out = await analytics_service.staff_task_times(
            db, project_id=seed["project"].id, user_id=seed["indexer"].id, task_type=TaskType.indexing,
        )
        assert out["rows"][0]["seconds"] == 45
        assert out["rows"][0]["outcome"] == "in_progress"
        assert out["rows"][0]["finished_at"] is None
        assert out["summary"]["finished"] == 0

    async def test_endpoint_json_csv_and_roles(self, db: AsyncSession, seed, client):
        now = datetime.now(timezone.utc)
        db.add(_done(seed, seconds=75, completed_at=now))
        await db.commit()
        url = (f"/api/analytics/staff-productivity/tasks?project_id={seed['project'].id}"
               f"&user_id={seed['indexer'].id}&task_type=indexing")
        sup = {"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": "digitizing"}
        r = await client.get(url, headers=sup)
        assert r.status_code == 200
        assert r.json()["rows"][0]["seconds"] == 75

        r = await client.get(url + "&format=csv", headers=sup)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        lines = r.text.strip().splitlines()
        assert lines[0].startswith("agent,record,")
        assert ",75," in lines[1]

        # QC detail is the customer's report, as qc-staff-productivity is.
        qc_url = url.replace("task_type=indexing", "task_type=qc")
        assert (await client.get(qc_url, headers=sup)).status_code == 403
        assert (await client.get(url + "&sort=bogus", headers=sup)).status_code == 422
        agent = {"Authorization": f"Bearer {token(seed['indexer'])}", "X-Portal": "digitizing"}
        assert (await client.get(url, headers=agent)).status_code == 403
        assert (await client.get(url.replace(f"project_id={seed['project'].id}", "project_id=99999"),
                                 headers=sup)).status_code == 404
