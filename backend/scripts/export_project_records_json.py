"""
Exports up to N records from one project as JSON — each record's identifiers,
status, location (cabinet/batch) and its indexed_data.

A record's project is resolved the same way export_records_report resolves
it: via its cabinet first, falling back to its batch's project.

Records come out in id order (oldest first) unless --random is given, which
draws a random sample instead. Output is a JSON array, or one record per line
with --jsonl. Writes to stdout unless --output is given; the summary line
goes to stderr so stdout can be piped.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.export_project_records_json --project-id 42 --count 10
    docker compose exec backend python -m scripts.export_project_records_json --project-id 42 --count 50 --random --status qa_passed
    docker compose exec backend python -m scripts.export_project_records_json --project-id 42 --count 1000 --jsonl --output /tmp/records.jsonl
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.batch import Batch
from app.models.cabinet import Cabinet
from app.models.project import Project
from app.models.record import Record, RecordStatus


async def fetch_records(
    db,
    *,
    project_id: int,
    count: int,
    statuses: list[RecordStatus] | None = None,
    indexed_only: bool = False,
    random: bool = False,
) -> list[dict]:
    resolved_project_id = func.coalesce(Cabinet.project_id, Batch.project_id)
    stmt = (
        select(Record, Cabinet.name.label("cabinet_name"), Batch.name.label("batch_name"))
        .outerjoin(Cabinet, Record.cabinet_id == Cabinet.id)
        .outerjoin(Batch, Record.batch_id == Batch.id)
        .where(resolved_project_id == project_id)
        .order_by(func.random() if random else Record.id)
        .limit(count)
    )
    if statuses:
        stmt = stmt.where(Record.status.in_(statuses))
    if indexed_only:
        stmt = stmt.where(Record.indexed_data.is_not(None))

    return [
        {
            "record_id": record.id,
            "source_identifier": record.source_identifier,
            "original_filename": record.original_filename,
            "file_reference": record.file_reference,
            "status": record.status.value,
            "current_version": record.current_version,
            "cabinet_id": record.cabinet_id,
            "cabinet_name": cabinet_name,
            "batch_id": record.batch_id,
            "batch_name": batch_name,
            "indexed_data": record.indexed_data,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }
        for record, cabinet_name, batch_name in (await db.execute(stmt)).all()
    ]


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project-id", type=int, required=True, help="The project to export from.")
    parser.add_argument("--count", "-n", type=int, required=True, help="How many records to output (at most).")
    parser.add_argument(
        "--status", action="append", choices=[s.value for s in RecordStatus], default=None,
        help="Restrict to one or more record statuses (repeatable).",
    )
    parser.add_argument("--indexed-only", action="store_true", help="Skip records with no indexed data yet.")
    parser.add_argument("--random", action="store_true", help="A random sample instead of the first N by id.")
    parser.add_argument("--jsonl", action="store_true", help="One JSON object per line instead of an array.")
    parser.add_argument("--output", type=Path, default=None, help="File to write. Defaults to stdout.")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be at least 1")

    async with AsyncSessionLocal() as db:
        if await db.get(Project, args.project_id) is None:
            print(f"Project {args.project_id} not found", file=sys.stderr)
            return 1
        records = await fetch_records(
            db,
            project_id=args.project_id,
            count=args.count,
            statuses=[RecordStatus(s) for s in args.status] if args.status else None,
            indexed_only=args.indexed_only,
            random=args.random,
        )

    if args.jsonl:
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    else:
        text = json.dumps(records, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)

    print(f"Wrote {len(records)} record(s) from project {args.project_id}"
          + (f" to {args.output}" if args.output else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
