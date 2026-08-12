"""
Bulk-sends every qa_passed record in one project back through QA, using the
same supervisor-requeue path the digitizing portal's Review & Requeue tab
calls (POST /api/records/requeue -> record_service.requeue_record(target=
"qa")) — just looped project-wide instead of a hand-picked selection. Each
record gets a fresh 1-record rework batch + pending QA task and its status
reset to qa_pending; a real requeued_for_qa audit event is written per
record, attributed to --performed-by-user-id, exactly as if a supervisor
had done it by hand. Nothing here is a workaround — this only calls the
app's own existing service function.

--performed-by-user-id must have role admin or de_supervisor — the same
requirement POST /api/records/requeue enforces via require_roles at the
router layer (record_service.requeue_record itself doesn't check role,
since it's normally only reachable through that router), so this script
checks it explicitly instead of silently allowing anyone.

Only qa_passed records are selected; requeue_record(target="qa") also
requires record.batch_id, which every qa_passed record already has.

Dry-run by default: prints what would change and makes no changes. Pass
--confirm to actually apply it. Each record is committed individually (same
pattern as scripts/generate_qc_training_data.py) so one failure doesn't
roll back records already requeued.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.requeue_qa_passed_records \\
        --project-id 42 --performed-by-user-id 7                       # dry run

    docker compose exec backend python -m scripts.requeue_qa_passed_records \\
        --project-id 42 --performed-by-user-id 7 --confirm --report out.json
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.batch import Batch
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.models.user import User, UserRole
from app.services import record_service

MAX_IDS_TO_PRINT = 50


async def _resolve_project(db: AsyncSession, project_id: int) -> Project:
    project = await db.get(Project, project_id)
    if not project:
        sys.exit(f"ERROR: no project with id={project_id}")
    return project


async def _resolve_performer(db: AsyncSession, user_id: int, tenant_id: int) -> User:
    user = await db.get(User, user_id)
    if not user:
        sys.exit(f"ERROR: no user with id={user_id}")
    if user.tenant_id != tenant_id:
        sys.exit(f"ERROR: user {user_id} is not in tenant {tenant_id}")
    if not user.is_active:
        sys.exit(f"ERROR: user {user_id} ({user.email}) is not active")
    if user.role not in (UserRole.admin, UserRole.de_supervisor):
        sys.exit(
            f"ERROR: user {user_id} ({user.email}) has role {user.role.value!r} — "
            f"must be admin or de_supervisor to requeue records (matches POST /api/records/requeue)"
        )
    return user


async def _find_qa_passed(db: AsyncSession, *, project_id: int, limit: int | None) -> list[Record]:
    batch_ids = list(
        (await db.execute(select(Batch.id).where(Batch.project_id == project_id))).scalars().all()
    )
    if not batch_ids:
        return []
    stmt = select(Record).where(
        Record.batch_id.in_(batch_ids), Record.status == RecordStatus.qa_passed
    ).order_by(Record.id)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument(
        "--performed-by-user-id", required=True, type=int,
        help="Real user id (admin or de_supervisor) to attribute every requeue event to.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max number of records to requeue.")
    parser.add_argument("--note", default=None, help="Optional note stored on each requeued_for_qa audit event.")
    parser.add_argument(
        "--confirm", action="store_true", help="Actually apply. Without this flag, dry run only."
    )
    parser.add_argument(
        "--report", type=Path, default=None, help="Where to write the JSON report (auto-named if omitted)."
    )
    args = parser.parse_args(argv)

    async with AsyncSessionLocal() as db:
        project = await _resolve_project(db, args.project_id)
        performer = await _resolve_performer(db, args.performed_by_user_id, project.tenant_id)
        tenant_id = project.tenant_id

        records = await _find_qa_passed(db, project_id=project.id, limit=args.limit)

        print(f"Project: {project.name!r} (id={project.id}, tenant_id={tenant_id})")
        print(f"Performed-by: {performer.full_name!r} ({performer.email}, role={performer.role.value})")
        print(f"qa_passed records found: {len(records)}")

        if not records:
            print("Nothing to do.")
            return 0

        ids = [r.id for r in records]
        if len(ids) <= MAX_IDS_TO_PRINT:
            print(f"  Record IDs: {ids}")

        if not args.confirm:
            print("\nDry run only — no changes made. Re-run with --confirm to apply.")
            if args.report:
                _write_report(args.report, project, performer, ids, [], [], dry_run=True)
                print(f"Dry-run report written to {args.report}")
            return 0

        requeued: list[int] = []
        failures: list[dict] = []
        for record in records:
            try:
                await record_service.requeue_record(
                    db, record_id=record.id, target="qa",
                    supervisor_id=performer.id, tenant_id=tenant_id, note=args.note,
                )
                await db.commit()
                requeued.append(record.id)
                print(f"  requeued record id={record.id} -> qa_pending")
            except Exception as exc:
                await db.rollback()
                failures.append({"record_id": record.id, "error": str(exc)})
                print(f"  FAILED record id={record.id}: {exc}", file=sys.stderr)

        print(f"\nRequeued {len(requeued)} of {len(records)} record(s). {len(failures)} failure(s).")

        report_path = args.report or Path(
            f"requeue_qa_report_project{project.id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        _write_report(report_path, project, performer, ids, requeued, failures, dry_run=False)
        print(f"Report written to {report_path}")

        if failures:
            return 1

    return 0


def _write_report(
    path: Path, project: Project, performer: User, candidates: list[int],
    requeued: list[int], failures: list[dict], *, dry_run: bool,
) -> None:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "project": {"id": project.id, "name": project.name, "tenant_id": project.tenant_id},
        "performed_by": {"id": performer.id, "email": performer.email, "role": performer.role.value},
        "candidates": candidates,
        "requeued": requeued,
        "failures": failures,
    }
    path.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
