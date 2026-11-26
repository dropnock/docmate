"""Time an agent spends on a task, added up across every session.

The workspace sends a heartbeat (POST /tasks/{id}/heartbeat) every
HEARTBEAT_SECONDS while the agent is using it: tab visible and some input
within the client's idle limit (5 minutes). Each heartbeat adds the time
since the previous one to ``Task.active_seconds``, unless the gap is longer
than MAX_GAP_SECONDS: then the agent was away (tab closed, idle, logged off)
and the gap counts for nothing. So leaving a record and coming back later
adds only the time actually spent in it.

``processing_time_seconds`` (what the productivity report averages) is set
from ``active_seconds`` whenever the task finishes. Tasks opened before
heartbeats existed have no ``last_active_at``; they keep the old measure,
last open to finish.
"""
from datetime import datetime, timezone

from app.models.task import Task, TaskStatus

HEARTBEAT_SECONDS = 30
MAX_GAP_SECONDS = 90


def _since(earlier: datetime, now: datetime) -> float:
    if earlier.tzinfo is None:  # SQLite (tests) drops the zone
        earlier = earlier.replace(tzinfo=timezone.utc)
    return (now - earlier).total_seconds()


def _add_gap(task: Task, now: datetime) -> None:
    if task.last_active_at is None:
        return
    gap = _since(task.last_active_at, now)
    if 0 < gap <= MAX_GAP_SECONDS:
        task.active_seconds = (task.active_seconds or 0) + int(round(gap))
    task.last_active_at = now


def begin_session(task: Task, now: datetime) -> None:
    """The agent opened the task. A start right after a heartbeat (a page
    reload, a second tab) continues the same session."""
    continuing = (
        task.status == TaskStatus.in_progress
        and task.last_active_at is not None
        and _since(task.last_active_at, now) <= MAX_GAP_SECONDS
    )
    if continuing:
        _add_gap(task, now)
        return
    if task.started_at is None:
        task.started_at = now
    if not task.sessions and task.processing_time_seconds:
        # Finished before heartbeats existed and now reopened: keep that time.
        task.active_seconds = task.processing_time_seconds
    task.sessions = (task.sessions or 0) + 1
    task.last_active_at = now


def heartbeat(task: Task, now: datetime) -> None:
    if task.last_active_at is None:
        # Opened before heartbeats existed: start counting from here.
        task.last_active_at = now
        return
    _add_gap(task, now)


def end_session(task: Task, now: datetime) -> None:
    """The task finished (completed, failed, skipped, requeued)."""
    if task.last_active_at is None:
        if task.started_at and not task.sessions:
            task.processing_time_seconds = int(_since(task.started_at, now))
        return
    _add_gap(task, now)
    task.processing_time_seconds = task.active_seconds or 0
    task.last_active_at = None


def reset(task: Task) -> None:
    """The task moved to another agent: their time starts from nothing."""
    task.started_at = None
    task.last_active_at = None
    task.active_seconds = 0
    task.sessions = 0
    task.processing_time_seconds = None
