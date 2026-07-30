"""
Exports a CSV report of records and their current state — one row per
record, across cabinets/batches, with the resolved project and (if locked)
who holds the lock.

A record's project is resolved the same way analytics_service.project_kpis
resolves it: via its cabinet first (so newly-ingested records not yet
assigned to a batch are still included), falling back to its batch's
project for the rare case a record is batch-linked but its cabinet_id is
unset.

Streams results in pages ordered by record id rather than loading the whole
table at once, since this can run against a large production dataset.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.export_records_report
    docker compose exec backend python -m scripts.export_records_report --output /tmp/records.csv
    docker compose exec backend python -m scripts.export_records_report --project-id 42
    docker compose exec backend python -m scripts.export_records_report --tenant-id 3 --status qa_failed --status qc_failed
"""
import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.batch import Batch
from app.models.cabinet import Cabinet
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.models.user import User

PAGE_SIZE = 2000

FIELDNAMES = [
    "record_id",
    "tenant_id",
    "project_id",
    "project_name",
    "cabinet_id",
    "cabinet_name",
    "batch_id",
    "batch_name",
    "batch_status",
    "status",
    "current_version",
    "source_identifier",
    "original_filename",
    "locked_by_email",
    "created_at",
    "updated_at",
]


def _base_query():
    resolved_project_id = func.coalesce(Cabinet.project_id, Batch.project_id)
    return (
        select(
            Record.id.label("record_id"),
            Project.tenant_id.label("tenant_id"),
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Record.cabinet_id,
            Cabinet.name.label("cabinet_name"),
            Record.batch_id,
            Batch.name.label("batch_name"),
            Batch.status.label("batch_status"),
            Record.status,
            Record.current_version,
            Record.source_identifier,
            Record.original_filename,
            User.email.label("locked_by_email"),
            Record.created_at,
            Record.updated_at,
        )
        .select_from(Record)
        .outerjoin(Cabinet, Record.cabinet_id == Cabinet.id)
        .outerjoin(Batch, Record.batch_id == Batch.id)
        .outerjoin(Project, Project.id == resolved_project_id)
        .outerjoin(User, Record.locked_by == User.id)
    )


async def _stream_rows(
    db: AsyncSession,
    *,
    tenant_id: int | None,
    project_id: int | None,
    statuses: list[RecordStatus] | None,
):
    """Yields one dict per record, ordered by record id, paged rather than
    fetched all at once."""
    last_id = 0
    while True:
        stmt = _base_query().where(Record.id > last_id).order_by(Record.id).limit(PAGE_SIZE)
        if tenant_id is not None:
            stmt = stmt.where(Project.tenant_id == tenant_id)
        if project_id is not None:
            stmt = stmt.where(Project.id == project_id)
        if statuses:
            stmt = stmt.where(Record.status.in_(statuses))

        rows = (await db.execute(stmt)).all()
        if not rows:
            return
        for row in rows:
            last_id = row.record_id
            yield {
                "record_id": row.record_id,
                "tenant_id": row.tenant_id,
                "project_id": row.project_id,
                "project_name": row.project_name,
                "cabinet_id": row.cabinet_id,
                "cabinet_name": row.cabinet_name,
                "batch_id": row.batch_id,
                "batch_name": row.batch_name,
                "batch_status": row.batch_status.value if row.batch_status else None,
                "status": row.status.value,
                "current_version": row.current_version,
                "source_identifier": row.source_identifier,
                "original_filename": row.original_filename,
                "locked_by_email": row.locked_by_email,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        if len(rows) < PAGE_SIZE:
            return


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="CSV path. Defaults to records_report_<timestamp>.csv in the current directory.",
    )
    parser.add_argument("--tenant-id", type=int, default=None, help="Restrict to one tenant.")
    parser.add_argument("--project-id", type=int, default=None, help="Restrict to one project.")
    parser.add_argument(
        "--status", action="append", choices=[s.value for s in RecordStatus], default=None,
        help="Restrict to one or more record statuses (repeatable).",
    )
    args = parser.parse_args(argv)

    output_path = args.output or Path(
        f"records_report_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv"
    )
    statuses = [RecordStatus(s) for s in args.status] if args.status else None

    count = 0
    async with AsyncSessionLocal() as db:
        with output_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
            writer.writeheader()
            async for row in _stream_rows(
                db, tenant_id=args.tenant_id, project_id=args.project_id, statuses=statuses
            ):
                writer.writerow(row)
                count += 1

    print(f"Wrote {count} record(s) to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
