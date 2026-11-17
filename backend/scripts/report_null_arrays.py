"""
Reports the qa_passed records in one project whose array fields are null.

For each qa_passed record, every top-level property its document type's
schema declares as an array (for Caveat: registered_titles,
registered_owners, caveators) is checked, and each one that isn't a
non-empty list gets a CSV row with its state:
  - null:     the key is present with value null
  - missing:  the key isn't in indexed_data at all
  - empty:    an empty list (left out with --null-only)
  - not_list: some other value (a string, an object, ...) (left out with --null-only)

A record with no indexed_data at all gets one row with field
"(indexed_data)". The document type comes from the record's batch; a
qa_passed record in the project's cabinets with no batch can't be checked
and is listed on stderr. Pass --field to check only some arrays.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.report_null_arrays --project-id 1
    docker compose exec backend python -m scripts.report_null_arrays --project-id 1 --field caveators --null-only --output /tmp/nulls.csv
Copy the file out of the container with:
    docker compose cp backend:/tmp/nulls.csv .
"""
import argparse
import asyncio
import csv
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.batch import Batch
from app.models.cabinet import Cabinet
from app.models.document_type import DocumentType
from app.models.project import Project
from app.models.record import Record, RecordStatus

FIELDNAMES = ["record_id", "caveat_number", "batch", "document_type", "image_filename", "field", "state"]
PAGE_SIZE = 500


def array_fields(schema: dict | None) -> list[str]:
    props = (schema or {}).get("properties") or {}
    return [k for k, v in props.items() if isinstance(v, dict) and v.get("type") == "array"]


def array_state(data: dict, field: str) -> str | None:
    """None when the field is a non-empty list, else what's wrong with it."""
    if field not in data:
        return "missing"
    value = data[field]
    if value is None:
        return "null"
    if not isinstance(value, list):
        return "not_list"
    return "empty" if not value else None


async def scan(
    db: AsyncSession, project_id: int, *, fields: list[str] | None = None, null_only: bool = False,
) -> tuple[list[dict], int, list[int]]:
    """Returns (report rows, qa_passed records scanned, unbatched qa_passed record ids)."""
    batches = {
        b.id: (b, dt)
        for b, dt in (
            await db.execute(
                select(Batch, DocumentType)
                .join(DocumentType, DocumentType.id == Batch.document_type_id)
                .where(Batch.project_id == project_id)
            )
        ).all()
    }
    rows: list[dict] = []
    scanned = 0
    last_id = 0
    while batches:
        records = list((await db.execute(
            select(Record)
            .where(Record.id > last_id, Record.batch_id.in_(list(batches)), Record.status == RecordStatus.qa_passed)
            .order_by(Record.id)
            .limit(PAGE_SIZE)
        )).scalars())
        for r in records:
            last_id = r.id
            scanned += 1
            batch, dt = batches[r.batch_id]
            data = r.indexed_data if isinstance(r.indexed_data, dict) else None
            base = {
                "record_id": r.id,
                "caveat_number": (data or {}).get("caveat_number") or "",
                "batch": batch.name,
                "document_type": dt.name,
                "image_filename": r.original_filename or r.file_reference or "",
            }
            if data is None:
                rows.append({**base, "field": "(indexed_data)", "state": "null"})
                continue
            for field in array_fields(dt.json_schema):
                if fields and field not in fields:
                    continue
                state = array_state(data, field)
                if state is None or (null_only and state in ("empty", "not_list")):
                    continue
                rows.append({**base, "field": field, "state": state})
        if len(records) < PAGE_SIZE:
            break

    unbatched = list((await db.execute(
        select(Record.id)
        .join(Cabinet, Cabinet.id == Record.cabinet_id)
        .where(Cabinet.project_id == project_id, Record.batch_id.is_(None), Record.status == RecordStatus.qa_passed)
        .order_by(Record.id)
    )).scalars())
    return rows, scanned, unbatched


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument("--field", action="append", help="Only check this array field (repeatable).")
    parser.add_argument("--null-only", action="store_true", help="Report only null/missing arrays, not empty ones.")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    output = args.output or Path(f"null_arrays_{args.project_id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv")

    async with AsyncSessionLocal() as db:
        project = await db.get(Project, args.project_id)
        if not project:
            sys.exit(f"ERROR: no project with id={args.project_id}")
        rows, scanned, unbatched = await scan(db, project.id, fields=args.field, null_only=args.null_only)

    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Project {project.name!r} (id={project.id}): {scanned} qa_passed records scanned, "
          f"{len({r['record_id'] for r in rows})} reported.")
    for (field, state), n in sorted(Counter((r["field"], r["state"]) for r in rows).items()):
        print(f"  {field:<22} {state:<9} {n}")
    print(f"Wrote {output}")
    if unbatched:
        print(f"{len(unbatched)} qa_passed record(s) have no batch and weren't checked: {unbatched[:50]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
