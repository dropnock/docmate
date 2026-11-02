"""
Exports a CSV of every qa_passed record in one project: its record name
(<caveat_number>_<volume>_<folio>, the same name export_caveat_record_files
gives its JSON file), the image filename, and who indexed and who QA'd it.

  - indexed_by: whoever completed the record's latest indexing task — the
    version that went on to pass QA. A record QA sent back for rework was
    indexed again, so earlier indexers aren't the ones credited here; see
    the record's Audit Timeline for the full history.
  - qa_by: whoever completed its latest QA task (the pass).
  - image_filename: the scan's original filename as uploaded, or, if that
    wasn't recorded, the stored image's filename.

A record whose name can't be built (no caveat number, or no volume/folio on
its first registered title) still gets a row, with record_name empty, and is
listed on stderr with the reason, so the report always covers every
qa_passed record. record_id is included to look a row up in the app.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.export_qa_passed_report --project-id 3
    docker compose exec backend python -m scripts.export_qa_passed_report --project-id 3 --output /tmp/qa_passed.csv
Copy the file out of the container with:
    docker compose cp backend:/tmp/qa_passed.csv .
"""
import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.batch import Batch
from app.models.cabinet import Cabinet
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.models.user import User
from scripts.export_caveat_record_files import file_stem

CHUNK = 2000
FIELDNAMES = ["record_name", "image_filename", "indexed_by", "qa_by", "record_id"]


async def _records(db, project_id: int) -> list[Record]:
    resolved_project_id = func.coalesce(Cabinet.project_id, Batch.project_id)
    return list((await db.execute(
        select(Record)
        .outerjoin(Cabinet, Record.cabinet_id == Cabinet.id)
        .outerjoin(Batch, Record.batch_id == Batch.id)
        .where(resolved_project_id == project_id, Record.status == RecordStatus.qa_passed)
        .order_by(Record.id)
    )).scalars())


async def _latest_completers(db, record_ids: list[int]) -> dict[tuple[int, TaskType], str]:
    """{(record_id, indexing|qa): full name} of whoever completed the latest
    task of that type on each record."""
    latest: dict[tuple[int, TaskType], str] = {}
    for start in range(0, len(record_ids), CHUNK):
        rows = (await db.execute(
            select(Task.record_id, Task.task_type, User.full_name)
            .join(User, User.id == Task.assigned_to)
            .where(
                Task.record_id.in_(record_ids[start:start + CHUNK]),
                Task.task_type.in_((TaskType.indexing, TaskType.qa)),
                Task.status == TaskStatus.completed,
            )
            .order_by(Task.completed_at, Task.id)
        )).all()
        for record_id, task_type, name in rows:
            latest[(record_id, task_type)] = name  # ordered oldest first, so the last one wins
    return latest


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-id", type=int, required=True, help="The project to report on.")
    parser.add_argument(
        "--output", type=Path, default=None,
        help="CSV path. Defaults to qa_passed_<project_id>_<timestamp>.csv in the current directory.",
    )
    args = parser.parse_args(argv)

    async with AsyncSessionLocal() as db:
        project = await db.get(Project, args.project_id)
        if project is None:
            print(f"Project {args.project_id} not found", file=sys.stderr)
            return 1
        records = await _records(db, project.id)
        people = await _latest_completers(db, [r.id for r in records])

    output = args.output or Path(f"qa_passed_{project.id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv")
    unnamed: list[tuple[int, str]] = []
    missing_people: list[int] = []
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for record in records:
            name, reason = file_stem(record.indexed_data)
            if name is None:
                unnamed.append((record.id, reason))
            indexed_by = people.get((record.id, TaskType.indexing))
            qa_by = people.get((record.id, TaskType.qa))
            if not indexed_by or not qa_by:
                missing_people.append(record.id)
            writer.writerow({
                "record_name": name or "",
                "image_filename": record.original_filename
                or (record.file_reference.rsplit("/", 1)[-1] if record.file_reference else ""),
                "indexed_by": indexed_by or "",
                "qa_by": qa_by or "",
                "record_id": record.id,
            })

    print(f"Project {project.id} {project.name!r}: {len(records)} qa_passed record(s) written to {output}")
    if unnamed:
        print(f"{len(unnamed)} record(s) have no record name (left empty):", file=sys.stderr)
        for record_id, reason in unnamed:
            print(f"  - record {record_id}: {reason}", file=sys.stderr)
    if missing_people:
        print(
            f"{len(missing_people)} record(s) have no completed indexing or QA task to credit "
            f"(e.g. imported already indexed): {', '.join(map(str, missing_people[:20]))}"
            + (" …" if len(missing_people) > 20 else ""),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
