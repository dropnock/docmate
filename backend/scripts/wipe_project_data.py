"""
One-time, irreversible cleanup: deletes every record for one project — plus
everything that references those records (record_versions, tasks,
lot_records, matching audit_logs) and their S3 images — along with any
batches/lots built from those records. The project itself and its cabinet(s)
are left in place, so the cabinet can be re-ingested into afterward.

Dry-run by default: prints exactly what would be deleted and makes no
changes. Pass --confirm to actually execute; you'll then be asked to
re-type the project's name as a final check (skip that prompt with --yes
for scripted/non-interactive runs — --confirm is still required either way).

Deleting audit_logs for these entities means the app's own audit trail can't
tell you this happened afterward, so a --confirm run always writes a JSON
report of every ID it touched (auto-named if --report isn't given) — that
report is the only record of what was removed.

Run inside the backend container/environment (needs direct DB + S3 access):
    docker compose exec backend python -m scripts.wipe_project_data \\
        --project-name NLA-Caveat-Cards                    # dry run

    docker compose exec backend python -m scripts.wipe_project_data \\
        --project-name NLA-Caveat-Cards --confirm           # asks to retype name

    docker compose exec backend python -m scripts.wipe_project_data \\
        --project-id 42 --confirm --yes --report out.json   # non-interactive
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditEntityType, AuditLog
from app.models.batch import Batch, BatchQCResult
from app.models.cabinet import Cabinet
from app.models.lot import Lot, LotRecord
from app.models.project import Project
from app.models.record import Record
from app.models.record_version import RecordVersion
from app.models.task import Task
from app.services import s3_service


def _s3_keys_for_record(record: Record) -> list[str]:
    """A record whose TIFF scan was already converted (cabinet_service.
    _convert_tiff_if_needed) has file_reference repointed at the derived
    .pdf, but the original .tif/.tiff is never deleted at conversion time —
    so for a .pdf reference, also try both original extensions. delete_object
    is a no-op if a key doesn't exist, so guessing wrong here is harmless."""
    if not record.file_reference:
        return []
    keys = [record.file_reference]
    if record.file_reference.lower().endswith(".pdf"):
        base = record.file_reference[: -len(".pdf")]
        keys.append(f"{base}.tif")
        keys.append(f"{base}.tiff")
    return keys


async def _resolve_project(
    db: AsyncSession, *, project_id: int | None, project_name: str | None, tenant_id: int | None
) -> Project:
    if project_id is not None:
        project = await db.get(Project, project_id)
        if not project:
            sys.exit(f"ERROR: no project with id={project_id}")
        if tenant_id is not None and project.tenant_id != tenant_id:
            sys.exit(f"ERROR: project {project_id} belongs to tenant {project.tenant_id}, not {tenant_id}")
        return project

    stmt = select(Project).where(Project.name == project_name)
    if tenant_id is not None:
        stmt = stmt.where(Project.tenant_id == tenant_id)
    matches = list((await db.execute(stmt)).scalars().all())
    if not matches:
        scope = f" in tenant {tenant_id}" if tenant_id is not None else ""
        sys.exit(f"ERROR: no project named {project_name!r}{scope}")
    if len(matches) > 1:
        lines = "\n".join(f"  id={p.id} tenant_id={p.tenant_id}" for p in matches)
        sys.exit(
            f"ERROR: {len(matches)} projects named {project_name!r} — disambiguate with "
            f"--project-id or --tenant-id:\n{lines}"
        )
    return matches[0]


async def _gather_scope(db: AsyncSession, project: Project) -> dict:
    cabinet_ids = list(
        (await db.execute(select(Cabinet.id).where(Cabinet.project_id == project.id))).scalars().all()
    )
    batch_ids = list(
        (await db.execute(select(Batch.id).where(Batch.project_id == project.id))).scalars().all()
    )
    lot_ids = list(
        (await db.execute(select(Lot.id).where(Lot.project_id == project.id))).scalars().all()
    )

    record_conditions = []
    if cabinet_ids:
        record_conditions.append(Record.cabinet_id.in_(cabinet_ids))
    if batch_ids:
        record_conditions.append(Record.batch_id.in_(batch_ids))
    records = (
        list((await db.execute(select(Record).where(or_(*record_conditions)))).scalars().all())
        if record_conditions
        else []
    )
    record_ids = [r.id for r in records]

    task_ids = []
    task_conditions = []
    if record_ids:
        task_conditions.append(Task.record_id.in_(record_ids))
    if batch_ids:
        task_conditions.append(Task.batch_id.in_(batch_ids))
    if task_conditions:
        task_ids = list((await db.execute(select(Task.id).where(or_(*task_conditions)))).scalars().all())

    lot_record_conditions = []
    if lot_ids:
        lot_record_conditions.append(LotRecord.lot_id.in_(lot_ids))
    if record_ids:
        lot_record_conditions.append(LotRecord.record_id.in_(record_ids))
    lot_record_count = 0
    if lot_record_conditions:
        lot_record_count = (
            await db.execute(select(func.count()).select_from(LotRecord).where(or_(*lot_record_conditions)))
        ).scalar_one()

    batch_qc_result_count = 0
    if batch_ids:
        batch_qc_result_count = (
            await db.execute(
                select(func.count()).select_from(BatchQCResult).where(BatchQCResult.batch_id.in_(batch_ids))
            )
        ).scalar_one()

    record_version_count = 0
    if record_ids:
        record_version_count = (
            await db.execute(
                select(func.count()).select_from(RecordVersion).where(RecordVersion.record_id.in_(record_ids))
            )
        ).scalar_one()

    audit_conditions = []
    if record_ids:
        audit_conditions.append(
            (AuditLog.entity_type == AuditEntityType.record) & (AuditLog.entity_id.in_(record_ids))
        )
    if task_ids:
        audit_conditions.append(
            (AuditLog.entity_type == AuditEntityType.task) & (AuditLog.entity_id.in_(task_ids))
        )
    if batch_ids:
        audit_conditions.append(
            (AuditLog.entity_type == AuditEntityType.batch) & (AuditLog.entity_id.in_(batch_ids))
        )
    audit_log_count = 0
    if audit_conditions:
        audit_log_count = (
            await db.execute(select(func.count()).select_from(AuditLog).where(or_(*audit_conditions)))
        ).scalar_one()

    s3_keys = sorted({key for r in records for key in _s3_keys_for_record(r)})

    return {
        "cabinet_ids": cabinet_ids,
        "batch_ids": batch_ids,
        "lot_ids": lot_ids,
        "record_ids": record_ids,
        "task_ids": task_ids,
        "lot_record_count": lot_record_count,
        "batch_qc_result_count": batch_qc_result_count,
        "record_version_count": record_version_count,
        "audit_log_count": audit_log_count,
        "s3_keys": s3_keys,
    }


def _print_summary(project: Project, scope: dict) -> None:
    print(f"Project: {project.name!r} (id={project.id}, tenant_id={project.tenant_id})")
    print(f"Cabinets kept, untouched: {scope['cabinet_ids']}")
    print()
    print("To be deleted:")
    print(f"  Records:            {len(scope['record_ids'])}")
    print(f"  Record versions:    {scope['record_version_count']}")
    print(f"  Tasks:              {len(scope['task_ids'])}")
    print(f"  Lot records:        {scope['lot_record_count']}")
    print(f"  Lots:               {len(scope['lot_ids'])}")
    print(f"  Batches:            {len(scope['batch_ids'])}")
    print(f"  Batch QC results:   {scope['batch_qc_result_count']}")
    print(f"  Audit log entries:  {scope['audit_log_count']}")
    print(f"  S3 objects (bucket={project.s3_bucket_name}): {len(scope['s3_keys'])}")


async def _delete_db_rows(db: AsyncSession, scope: dict) -> None:
    """Deepest-referencing tables first, so FK constraints are never violated
    mid-transaction regardless of DB engine defer settings."""
    record_ids, task_ids = scope["record_ids"], scope["task_ids"]
    batch_ids, lot_ids = scope["batch_ids"], scope["lot_ids"]

    audit_conditions = []
    if record_ids:
        audit_conditions.append(
            (AuditLog.entity_type == AuditEntityType.record) & (AuditLog.entity_id.in_(record_ids))
        )
    if task_ids:
        audit_conditions.append(
            (AuditLog.entity_type == AuditEntityType.task) & (AuditLog.entity_id.in_(task_ids))
        )
    if batch_ids:
        audit_conditions.append(
            (AuditLog.entity_type == AuditEntityType.batch) & (AuditLog.entity_id.in_(batch_ids))
        )
    if audit_conditions:
        await db.execute(delete(AuditLog).where(or_(*audit_conditions)))

    lot_record_conditions = []
    if lot_ids:
        lot_record_conditions.append(LotRecord.lot_id.in_(lot_ids))
    if record_ids:
        lot_record_conditions.append(LotRecord.record_id.in_(record_ids))
    if lot_record_conditions:
        await db.execute(delete(LotRecord).where(or_(*lot_record_conditions)))

    if lot_ids:
        await db.execute(delete(Lot).where(Lot.id.in_(lot_ids)))

    if batch_ids:
        await db.execute(delete(BatchQCResult).where(BatchQCResult.batch_id.in_(batch_ids)))

    task_conditions = []
    if record_ids:
        task_conditions.append(Task.record_id.in_(record_ids))
    if batch_ids:
        task_conditions.append(Task.batch_id.in_(batch_ids))
    if task_conditions:
        await db.execute(delete(Task).where(or_(*task_conditions)))

    if record_ids:
        await db.execute(delete(RecordVersion).where(RecordVersion.record_id.in_(record_ids)))
        await db.execute(delete(Record).where(Record.id.in_(record_ids)))

    if batch_ids:
        await db.execute(delete(Batch).where(Batch.id.in_(batch_ids)))


async def _delete_s3_objects(project: Project, scope: dict) -> list[tuple[str, str]]:
    if not project.s3_bucket_name:
        return []
    failures = []
    for key in scope["s3_keys"]:
        try:
            await s3_service.delete_object(project.s3_bucket_name, key)
        except Exception as exc:
            failures.append((key, str(exc)))
    return failures


def _write_report(
    path: Path, project: Project, scope: dict, *, dry_run: bool, s3_failures: list[tuple[str, str]]
) -> None:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "project": {"id": project.id, "name": project.name, "tenant_id": project.tenant_id},
        "cabinets_kept": scope["cabinet_ids"],
        "deleted": {
            "record_ids": scope["record_ids"],
            "task_ids": scope["task_ids"],
            "lot_ids": scope["lot_ids"],
            "batch_ids": scope["batch_ids"],
            "record_version_count": scope["record_version_count"],
            "lot_record_count": scope["lot_record_count"],
            "batch_qc_result_count": scope["batch_qc_result_count"],
            "audit_log_count": scope["audit_log_count"],
        },
        "s3": {
            "bucket": project.s3_bucket_name,
            "keys_attempted": scope["s3_keys"],
            "delete_failures": [{"key": k, "error": e} for k, e in s3_failures],
        },
    }
    path.write_text(json.dumps(report, indent=2))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    id_group = parser.add_mutually_exclusive_group(required=True)
    id_group.add_argument("--project-id", type=int)
    id_group.add_argument("--project-name")
    parser.add_argument(
        "--tenant-id", type=int, default=None, help="Disambiguate --project-name across tenants."
    )
    parser.add_argument(
        "--confirm", action="store_true", help="Actually delete. Without this flag, dry run only."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive re-type-the-project-name prompt (still requires --confirm).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Where to write the JSON report. Auto-named in the current directory if omitted on a --confirm run.",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    async with AsyncSessionLocal() as db:
        project = await _resolve_project(
            db, project_id=args.project_id, project_name=args.project_name, tenant_id=args.tenant_id
        )
        scope = await _gather_scope(db, project)
        _print_summary(project, scope)

        if not args.confirm:
            print("\nDry run only — no changes made. Re-run with --confirm to execute.")
            if args.report:
                _write_report(args.report, project, scope, dry_run=True, s3_failures=[])
                print(f"Dry-run report written to {args.report}")
            return 0

        if not args.yes:
            typed = input(f"\nType the project name exactly to confirm deletion ({project.name!r}): ")
            if typed != project.name:
                print("ERROR: project name did not match — aborting, no changes made.", file=sys.stderr)
                return 2

        await _delete_db_rows(db, scope)
        await db.commit()
        print("\nDatabase rows deleted.")

        s3_failures = await _delete_s3_objects(project, scope)

        report_path = args.report or Path(
            f"wipe_report_project{project.id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        _write_report(report_path, project, scope, dry_run=False, s3_failures=s3_failures)
        print(f"Report written to {report_path}")

        if s3_failures:
            print(f"\nWARNING: {len(s3_failures)} S3 object(s) failed to delete:", file=sys.stderr)
            for key, err in s3_failures:
                print(f"  - {key}: {err}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
