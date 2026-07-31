"""
Exports the raw per-user, per-task-type error rate: how many of a user's
completed vs. failed tasks were QA/QC rejections, as raw counts plus the
computed ratio — the same `failed / (completed + failed)` calculation
analytics_service.staff_productivity() uses, but flattened across every
project a user has touched rather than scoped to one project at a time.

Requires the task_service.fail_task() fix (tasks.status is correctly set to
`failed` on QA/QC rejection) to be reflected in the data — run
scripts/backfill_task_failed_status.py first against a database with
pre-fix history.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.export_user_error_rates
    docker compose exec backend python -m scripts.export_user_error_rates --output /tmp/error_rates.csv
    docker compose exec backend python -m scripts.export_user_error_rates --project-id 42
    docker compose exec backend python -m scripts.export_user_error_rates --tenant-id 3
"""
import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.batch import Batch
from app.models.project import Project
from app.models.task import Task, TaskStatus
from app.models.user import User

FIELDNAMES = [
    "user_id",
    "full_name",
    "email",
    "role",
    "task_type",
    "completed_count",
    "failed_count",
    "total_attempted",
    "error_rate",
]


async def _fetch_rows(
    db: AsyncSession, *, tenant_id: int | None, project_id: int | None
) -> list[dict]:
    completed_expr = func.sum(case((Task.status == TaskStatus.completed, 1), else_=0))
    failed_expr = func.sum(case((Task.status == TaskStatus.failed, 1), else_=0))

    stmt = (
        select(
            User.id,
            User.full_name,
            User.email,
            User.role,
            Task.task_type,
            completed_expr.label("completed_count"),
            failed_expr.label("failed_count"),
        )
        .select_from(Task)
        .join(User, Task.assigned_to == User.id)
        .join(Batch, Task.batch_id == Batch.id)
        .join(Project, Batch.project_id == Project.id)
        .where(Task.status.in_([TaskStatus.completed, TaskStatus.failed]))
        .group_by(User.id, User.full_name, User.email, User.role, Task.task_type)
        .order_by(User.full_name, Task.task_type)
    )
    if tenant_id is not None:
        stmt = stmt.where(Project.tenant_id == tenant_id)
    if project_id is not None:
        stmt = stmt.where(Project.id == project_id)

    rows = (await db.execute(stmt)).all()

    result = []
    for user_id, full_name, email, role, task_type, completed_count, failed_count in rows:
        total_attempted = completed_count + failed_count
        error_rate = round(failed_count / total_attempted, 4) if total_attempted else 0.0
        result.append({
            "user_id": user_id,
            "full_name": full_name,
            "email": email,
            "role": role.value,
            "task_type": task_type.value,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "total_attempted": total_attempted,
            "error_rate": error_rate,
        })
    return result


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="CSV path. Defaults to user_error_rates_<timestamp>.csv in the current directory.",
    )
    parser.add_argument("--tenant-id", type=int, default=None, help="Restrict to one tenant.")
    parser.add_argument("--project-id", type=int, default=None, help="Restrict to one project.")
    args = parser.parse_args(argv)

    output_path = args.output or Path(
        f"user_error_rates_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv"
    )

    async with AsyncSessionLocal() as db:
        rows = await _fetch_rows(db, tenant_id=args.tenant_id, project_id=args.project_id)

    with output_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} row(s) to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
