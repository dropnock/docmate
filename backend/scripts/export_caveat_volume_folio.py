"""
Exports a CSV of caveat_number / volume_number / folio_number / image_filename
from qa_passed records in one project — one row per (volume_number,
folio_number) grouping, so a record with N groupings produces N rows all
sharing the same caveat_number and image_filename.

indexed_data is free-form JSON driven by the project's DocumentType.json_schema
(see app/models/document_type.py), so this doesn't assume a fixed key name for
the groupings array — the same way frontend/src/shared/components/rjsf/
CustomWidgets.tsx's ParcelArrayField locates volume/folio keys inside whatever
array the schema defines them under. Concretely, for each record:
  - caveat_number is read as indexed_data["caveat_number"].
  - The groupings array is whichever top-level value in indexed_data is a
    list of dicts where at least one item has a "volume_number" or
    "folio_number" key. If a record's schema puts groupings under a
    different shape than that, it's reported as skipped rather than guessed at.
  - image_filename is Record.file_reference — the S3 key of the image
    currently associated with the record, i.e. the file that's actually
    findable in the project's bucket right now. (Not original_filename,
    the uploader's original name — file_reference is what a TIFF-to-PDF
    conversion repoints at the derived .pdf, so it's the one that matches
    what's really in storage. Left blank if the record has no image.)

A record missing caveat_number, with no such array, or with an empty array
is skipped and reported to stderr — it contributes 0 rows rather than a row
with blanks, so the CSV never silently implies a grouping that isn't there.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.export_caveat_volume_folio --project-id 42
    docker compose exec backend python -m scripts.export_caveat_volume_folio --project-id 42 --output /tmp/out.csv
"""
import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.batch import Batch
from app.models.cabinet import Cabinet
from app.models.project import Project
from app.models.record import Record, RecordStatus

PAGE_SIZE = 2000
FIELDNAMES = ["caveat_number", "volume_number", "folio_number", "image_filename"]


def _find_groupings(indexed_data: dict) -> list | None:
    """Returns the first top-level array in indexed_data that looks like a
    list of volume/folio groupings, or None if nothing matches."""
    for value in indexed_data.values():
        if not isinstance(value, list) or not value:
            continue
        if all(isinstance(item, dict) for item in value) and any(
            "volume_number" in item or "folio_number" in item for item in value
        ):
            return value
    return None


def _rows_for_record(record: Record) -> tuple[list[dict], str | None]:
    """Returns (rows, skip_reason). skip_reason is None on success."""
    data = record.indexed_data
    if not isinstance(data, dict):
        return [], "indexed_data is missing or not an object"

    caveat_number = data.get("caveat_number")
    if caveat_number is None:
        return [], "no caveat_number field"

    groupings = _find_groupings(data)
    if groupings is None:
        return [], "no volume_number/folio_number array found"
    if not groupings:
        return [], "volume/folio array is empty"

    rows = [
        {
            "caveat_number": caveat_number,
            "volume_number": item.get("volume_number"),
            "folio_number": item.get("folio_number"),
            "image_filename": record.file_reference,
        }
        for item in groupings
    ]
    return rows, None


async def _resolve_project(db: AsyncSession, project_id: int) -> Project:
    project = await db.get(Project, project_id)
    if not project:
        sys.exit(f"ERROR: no project with id={project_id}")
    return project


async def _stream_records(db: AsyncSession, *, project_id: int):
    """Yields qa_passed Record rows for the project, paged by id."""
    cabinet_ids = list(
        (await db.execute(select(Cabinet.id).where(Cabinet.project_id == project_id))).scalars().all()
    )
    batch_ids = list(
        (await db.execute(select(Batch.id).where(Batch.project_id == project_id))).scalars().all()
    )
    conditions = []
    if cabinet_ids:
        conditions.append(Record.cabinet_id.in_(cabinet_ids))
    if batch_ids:
        conditions.append(Record.batch_id.in_(batch_ids))
    if not conditions:
        return

    last_id = 0
    while True:
        stmt = (
            select(Record)
            .where(Record.id > last_id, or_(*conditions), Record.status == RecordStatus.qa_passed)
            .order_by(Record.id)
            .limit(PAGE_SIZE)
        )
        rows = list((await db.execute(stmt)).scalars().all())
        if not rows:
            return
        for record in rows:
            last_id = record.id
            yield record
        if len(rows) < PAGE_SIZE:
            return


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument(
        "--output", type=Path, default=None,
        help="CSV path. Defaults to caveat_volume_folio_<project_id>_<timestamp>.csv in the current directory.",
    )
    args = parser.parse_args(argv)

    output_path = args.output or Path(
        f"caveat_volume_folio_{args.project_id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv"
    )

    record_count = 0
    row_count = 0
    skipped: list[tuple[int, str]] = []

    async with AsyncSessionLocal() as db:
        project = await _resolve_project(db, args.project_id)

        with output_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
            writer.writeheader()
            async for record in _stream_records(db, project_id=args.project_id):
                record_count += 1
                rows, skip_reason = _rows_for_record(record)
                if skip_reason:
                    skipped.append((record.id, skip_reason))
                    continue
                writer.writerows(rows)
                row_count += len(rows)

    print(
        f"Project: {project.name!r} (id={project.id}). "
        f"qa_passed records scanned: {record_count}. Rows written: {row_count}. "
        f"Skipped: {len(skipped)}."
    )
    print(f"Wrote {output_path}")

    if skipped:
        print(f"\n{len(skipped)} record(s) contributed no rows:", file=sys.stderr)
        for record_id, reason in skipped:
            print(f"  - record {record_id}: {reason}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
