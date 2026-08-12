"""
Bulk-advances qa_pending records in one project to qa_passed, so customer QC
agents have real qa_passed records to practice on. Built for standing up
customer QC training data, not for clearing a real QA backlog.

This does NOT fabricate a review. Every record is pushed through the exact
same task_service.complete_task() code path a QA agent's own "Done" click
would run — same lock acquire/release, same RecordVersion snapshot, same
qa_passed audit event, same batch auto-completion check — just with
indexed_data resubmitted unchanged (nothing is actually re-checked for
defects). --performed-by-user-id must be a real, existing user in the
project's tenant; every event this script writes (reassignment, task
start/complete, and the extra note below) is attributed to them, not to a
generic "admin" label, because that is who is actually taking the action by
running this script.

Because the QA step is genuinely being skipped rather than genuinely
performed, an additional audit_logs entry is written for each record right
after complete_task()'s own event, explicitly flagging the bypass:
    action=status_changed, metadata={"bulk_script": ..., "qa_review_bypassed": True}
Anyone reading get_record_history() later sees both that the record reached
qa_passed AND that it got there via this script, not a genuine review — the
whole point of audit_logs is to answer "what actually happened," so a
qa_passed record with no bypass note would misrepresent it as reviewed.

Only touches records with an open (pending/in_progress) QA task attached —
that's how a genuine qa_pending record always arrives (auto_advance_to_qa
creates the QA task at the same time it sets the record's status). A
qa_pending record with no open QA task is a data anomaly and is skipped,
reported as a failure rather than guessed at.

Dry-run by default: prints what would change and makes no changes. Pass
--confirm to actually apply it. Each record is committed individually (like
scripts/migrate_records_to_training.py) so one failure doesn't roll back
records already processed.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.generate_qc_training_data \\
        --project-id 42 --performed-by-user-id 7                       # dry run

    docker compose exec backend python -m scripts.generate_qc_training_data \\
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
from app.models.audit_log import AuditAction, AuditEntityType
from app.models.batch import Batch
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.models.user import User, UserRole
from app.services import audit_service, task_service

MAX_IDS_TO_PRINT = 50
BYPASS_NOTE = (
    "Generated as customer QC training data by scripts/generate_qc_training_data.py "
    "— indexed_data resubmitted unchanged; no genuine per-record QA review was performed."
)


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
            f"must be admin or de_supervisor to own these QA tasks"
        )
    return user


async def _find_qa_pending(
    db: AsyncSession, *, project_id: int, limit: int | None
) -> list[tuple[Record, Task]]:
    batch_ids = list(
        (await db.execute(select(Batch.id).where(Batch.project_id == project_id))).scalars().all()
    )
    if not batch_ids:
        return []

    stmt = select(Record).where(
        Record.batch_id.in_(batch_ids), Record.status == RecordStatus.qa_pending
    ).order_by(Record.id)
    if limit is not None:
        stmt = stmt.limit(limit)
    records = list((await db.execute(stmt)).scalars().all())

    pairs: list[tuple[Record, Task]] = []
    for record in records:
        task = (await db.execute(
            select(Task).where(
                Task.record_id == record.id,
                Task.task_type == TaskType.qa,
                Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
            ).order_by(Task.id.desc())
        )).scalars().first()
        if task:
            pairs.append((record, task))
    return pairs


async def _advance_one(
    db: AsyncSession, *, record: Record, task: Task, performer: User, tenant_id: int
) -> None:
    if task.assigned_to != performer.id:
        await task_service.reassign_task(
            db, task_id=task.id, new_agent_id=performer.id,
            supervisor_id=performer.id, tenant_id=tenant_id,
        )
    if task.status == TaskStatus.pending:
        await task_service.start_task(db, task_id=task.id, user_id=performer.id, tenant_id=tenant_id)

    await task_service.complete_task(
        db, task_id=task.id, user_id=performer.id, tenant_id=tenant_id,
        indexed_data=record.indexed_data,
    )

    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.record, entity_id=record.id,
        action=AuditAction.status_changed, performed_by=performer.id,
        old_value={"status": "qa_pending"}, new_value={"status": "qa_passed"},
        metadata={"bulk_script": "generate_qc_training_data.py", "qa_review_bypassed": True, "note": BYPASS_NOTE},
    )


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument(
        "--performed-by-user-id", required=True, type=int,
        help="Real user id (admin or de_supervisor) to attribute every event to — this must be "
             "whoever is actually running the script.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max number of records to advance.")
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

        pairs = await _find_qa_pending(db, project_id=project.id, limit=args.limit)
        total_qa_pending = len(list(
            (await db.execute(
                select(Record.id).join(Batch, Record.batch_id == Batch.id).where(
                    Batch.project_id == project.id, Record.status == RecordStatus.qa_pending,
                )
            )).scalars().all()
        ))
        no_open_task = total_qa_pending - len(pairs)

        print(f"Project: {project.name!r} (id={project.id}, tenant_id={tenant_id})")
        print(f"Performed-by: {performer.full_name!r} ({performer.email}, role={performer.role.value})")
        print(f"qa_pending records found: {total_qa_pending}   with an open QA task: {len(pairs)}")
        if no_open_task:
            print(f"  WARNING: {no_open_task} qa_pending record(s) have no open QA task — skipped, not touched.")

        if not pairs:
            print("Nothing to do.")
            return 0

        ids = [r.id for r, _ in pairs]
        if len(ids) <= MAX_IDS_TO_PRINT:
            print(f"  Record IDs: {ids}")

        if not args.confirm:
            print("\nDry run only — no changes made. Re-run with --confirm to apply.")
            if args.report:
                _write_report(args.report, project, performer, ids, [], [], dry_run=True)
                print(f"Dry-run report written to {args.report}")
            return 0

        advanced: list[int] = []
        failures: list[dict] = []
        for record, task in pairs:
            try:
                await _advance_one(db, record=record, task=task, performer=performer, tenant_id=tenant_id)
                await db.commit()
                advanced.append(record.id)
                print(f"  advanced record id={record.id} (task id={task.id}) -> qa_passed")
            except Exception as exc:
                await db.rollback()
                failures.append({"record_id": record.id, "task_id": task.id, "error": str(exc)})
                print(f"  FAILED record id={record.id}: {exc}", file=sys.stderr)

        print(f"\nAdvanced {len(advanced)} of {len(pairs)} record(s). {len(failures)} failure(s).")

        report_path = args.report or Path(
            f"qc_training_data_report_project{project.id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        _write_report(report_path, project, performer, ids, advanced, failures, dry_run=False)
        print(f"Report written to {report_path}")

        if failures:
            return 1

    return 0


def _write_report(
    path: Path, project: Project, performer: User, candidates: list[int],
    advanced: list[int], failures: list[dict], *, dry_run: bool,
) -> None:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "project": {"id": project.id, "name": project.name, "tenant_id": project.tenant_id},
        "performed_by": {"id": performer.id, "email": performer.email, "role": performer.role.value},
        "candidates": candidates,
        "advanced": advanced,
        "failures": failures,
    }
    path.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
