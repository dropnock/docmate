"""
Refresh a training project inside one DocMate deployment: clean out all of
its records, batches, lots, tasks and images (scripts.wipe_project_data),
then copy qa_passed records — indexed data and image — from another project
into the training project's cabinet.

Asks for everything when run: it lists the projects (with record and
qa_passed counts), then asks for the training project, the project to copy
from, how many records, and — if the training project has several — which
cabinet. It then prints what would be deleted and copied and asks you to
retype the training project's name before changing anything.

    docker compose exec backend python -m scripts.refresh_training_project
    docker compose exec backend python -m scripts.refresh_training_project --dry-run

Copied records land in the training cabinet as qa_passed with no batch, the
same way scripts.migrate_records_to_training places them, each image under a
fresh key in the training project's bucket. Records are picked newest first
(--order oldest|random to change). A JSON report of everything deleted and
copied is written to the current directory (inside the container unless
--report points at a mounted path).
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.batch import Batch
from app.models.cabinet import Cabinet
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.services import s3_service
from scripts import wipe_project_data as wipe


def _in_project(project_id: int):
    return or_(
        Record.cabinet_id.in_(select(Cabinet.id).where(Cabinet.project_id == project_id)),
        Record.batch_id.in_(select(Batch.id).where(Batch.project_id == project_id)),
    )


async def _select_source_records(
    db: AsyncSession, project: Project, *, limit: int, order: str,
) -> list[Record]:
    stmt = select(Record).where(
        _in_project(project.id),
        Record.status == RecordStatus.qa_passed,
        Record.file_reference.is_not(None),
    )
    if order == "oldest":
        stmt = stmt.order_by(Record.id.asc())
    elif order == "random":
        stmt = stmt.order_by(func.random())
    else:
        stmt = stmt.order_by(Record.id.desc())
    return list((await db.execute(stmt.limit(limit))).scalars().all())


async def _count_qa_passed(db: AsyncSession, project: Project) -> int:
    return (await db.execute(
        select(func.count()).select_from(Record).where(
            _in_project(project.id),
            Record.status == RecordStatus.qa_passed,
            Record.file_reference.is_not(None),
        )
    )).scalar_one()


async def _prompt_cabinet(db: AsyncSession, project: Project) -> Cabinet:
    cabinets = list((await db.execute(
        select(Cabinet).where(Cabinet.project_id == project.id).order_by(Cabinet.id)
    )).scalars().all())
    if not cabinets:
        sys.exit(f"ERROR: training project {project.name!r} has no cabinet — create one in the app first.")
    if len(cabinets) == 1:
        return cabinets[0]
    print("Cabinets in the training project:")
    for c in cabinets:
        print(f"  {c.id:>5}  {c.name}")
    by_id = {c.id: c for c in cabinets}
    while True:
        answer = input("Copy into which cabinet (id): ").strip()
        if answer.isdigit() and int(answer) in by_id:
            return by_id[int(answer)]
        print("  Not one of the cabinets above.")


def _prompt_count(default: int) -> int:
    while True:
        answer = input(f"How many qa_passed records to copy [{default}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and int(answer) > 0:
            return int(answer)
        print("  Enter a positive whole number.")


async def _copy_records(
    db: AsyncSession, records: list[Record], *, target_project: Project, cabinet: Cabinet,
) -> tuple[list[dict], list[dict]]:
    """Copy each record's image into the training bucket under a fresh key
    and insert a qa_passed record in the training cabinet. Commits every 50
    records so a failure late in the run keeps what was already copied."""
    copied: list[dict] = []
    failures: list[dict] = []
    for i, record in enumerate(records, 1):
        try:
            source_project = await s3_service.resolve_record_project(record, db)
            if not source_project or not source_project.s3_bucket_name:
                raise RuntimeError("no resolvable source bucket")
            data = await s3_service.get_object_bytes(source_project.s3_bucket_name, record.file_reference)
            content_type = await s3_service.resolve_content_type(
                source_project.s3_bucket_name, record.file_reference
            )
            key = s3_service.record_image_key(
                target_project.id, cabinet.id, record.file_reference.rsplit("/", 1)[-1]
            )
            await s3_service.put_object_bytes(target_project.s3_bucket_name, key, data, content_type)
            new = Record(
                cabinet_id=cabinet.id,
                batch_id=None,
                file_reference=key,
                original_filename=record.original_filename,
                source_identifier=record.source_identifier,
                indexed_data=record.indexed_data,
                current_version=1,
                status=RecordStatus.qa_passed,
            )
            db.add(new)
            await db.flush()
            copied.append({"source_record_id": record.id, "training_record_id": new.id, "key": key})
        except Exception as exc:  # noqa: BLE001 — report and carry on
            failures.append({"source_record_id": record.id, "error": str(exc)})
            print(f"  FAILED record {record.id}: {exc}", file=sys.stderr)
        if i % 50 == 0:
            await db.commit()
            print(f"  {i}/{len(records)} done", flush=True)
    await db.commit()
    return copied, failures


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen, change nothing.")
    parser.add_argument("--default-count", type=int, default=1000, help="Default offered at the count prompt.")
    parser.add_argument("--order", choices=["newest", "oldest", "random"], default="newest")
    parser.add_argument("--report", type=Path, default=None, help="JSON report path (auto-named if omitted).")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    async with AsyncSessionLocal() as db:
        await wipe._print_projects(db)
        training = await wipe._prompt_project(db, "Training project to clean out")
        source = await wipe._prompt_project(db, "Copy qa_passed records from project", exclude=training.id)
        count = _prompt_count(args.default_count)
        cabinet = await _prompt_cabinet(db, training)
        if not training.s3_bucket_name:
            sys.exit(f"ERROR: training project {training.name!r} has no S3 bucket provisioned.")

        scope = await wipe._gather_scope(db, training)
        available = await _count_qa_passed(db, source)
        to_copy = min(count, available)

        print("\n── Clean out ─────────────────────────────────────")
        wipe._print_summary(training, scope)
        print("\n── Copy ──────────────────────────────────────────")
        print(f"From:  {source.name!r} (id={source.id}) — {available} qa_passed record(s) with an image")
        print(f"Into:  cabinet {cabinet.name!r} (id={cabinet.id}) of {training.name!r}, bucket {training.s3_bucket_name}")
        picked = "picked at random" if args.order == "random" else f"{args.order} first"
        print(f"Copy:  {to_copy} record(s), {picked}")
        if to_copy < count:
            print(f"       (asked for {count}, only {available} available)")

        if args.dry_run:
            print("\nDry run — nothing changed.")
            return 0

        typed = input(f"\nType the training project's name to go ahead ({training.name!r}): ")
        if typed != training.name:
            print("Name did not match — nothing changed.", file=sys.stderr)
            return 2

        await wipe._delete_db_rows(db, scope)
        await db.commit()
        print("\nTraining project cleaned out (database).")
        s3_failures = await wipe._delete_s3_objects(training, scope)
        print(f"Deleted images: {len(scope['s3_keys']) - len(s3_failures)} of {len(scope['s3_keys'])}")

        records = await _select_source_records(db, source, limit=to_copy, order=args.order)
        print(f"\nCopying {len(records)} record(s)…")
        copied, failures = await _copy_records(db, records, target_project=training, cabinet=cabinet)
        print(f"Copied {len(copied)} of {len(records)} record(s); {len(failures)} failure(s).")

        report_path = args.report or Path(
            f"training_refresh_project{training.id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        report_path.write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "training_project": {"id": training.id, "name": training.name, "cabinet_id": cabinet.id},
            "source_project": {"id": source.id, "name": source.name},
            "wiped": {
                "record_ids": scope["record_ids"], "task_ids": scope["task_ids"],
                "lot_ids": scope["lot_ids"], "batch_ids": scope["batch_ids"],
                "s3_keys": scope["s3_keys"],
                "s3_delete_failures": [{"key": k, "error": e} for k, e in s3_failures],
            },
            "copied": copied,
            "copy_failures": failures,
        }, indent=2))
        print(f"Report written to {report_path}")
        return 1 if failures or s3_failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
