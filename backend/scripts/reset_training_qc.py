"""
Reset a training project's customer QC so it can be run again: every lot the
customer has sampled goes back to the moment it was released ("assigned to
the customer for QC") — released, unsampled, no QC batches — so the customer
supervisor can sample it and assign QC agents afresh.

For each lot that has been sampled (it has a `sampled` audit event), this
undoes everything that happened from that sample onward:

  - QC batches and their tasks are deleted, and so is the rework that customer
    rejections set off: "QC Rework" batches, their QA/indexing tasks, and the
    batches and tasks of a resubmitted lot's re-inspection rework.
  - Customer QC marks (qc_field_results) and archived rounds (lot_qc_rounds)
    are deleted. The lot's sample/result fields are cleared, qc_round goes
    back to 1 and the status to released. A resubmitted lot gets its original
    release time back, and its superseded inspection is marked passed again;
    the re-inspections made after the resubmit are deleted.
  - Every lot record goes back to qa_passed, unsampled and unlocked, in the
    batch of its last task before QC (no batch if it had none, as with
    training records copied in by refresh_training_project). Record versions written by rework are deleted
    and indexed_data is restored from the last version before QC. A record
    with no earlier version (training records copied in by
    refresh_training_project start without one) keeps its reworked data;
    the report lists these records.
  - Flags and holds raised from the first sample onward are deleted.
  - Audit events for all of the above are deleted: from the lot's first
    sample onward for the lot and its records, plus every event of the
    deleted tasks and batches. The project's AQL switching state goes back
    to what it was before the first QC verdict, with its counters zeroed.

Lots that were never sampled are left alone. The audit trail can't show the
reset afterward, so a --confirm run always writes a JSON report of every id
it touched (auto-named if --report isn't given).

Dry run by default. Pass --confirm to apply; you are then asked to retype
the project's name (--yes skips that prompt).

    docker compose exec backend python -m scripts.reset_training_qc                       # asks, dry run
    docker compose exec backend python -m scripts.reset_training_qc --project-name Training
    docker compose exec backend python -m scripts.reset_training_qc --project-name Training --confirm
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.aql import AQLConfig, AQLStatus
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.batch import Batch, BatchQCResult, BatchType
from app.models.lot import Lot, LotQcRound, LotRecord, LotStatus
from app.models.lot_inspection import (
    LotInspection, LotInspectionFieldResult, LotInspectionItem, LotInspectionStatus,
)
from app.models.notification import Notification
from app.models.project import Project
from app.models.qc_field_result import QcFieldResult
from app.models.record import Record, RecordStatus
from app.models.record_flag import FlagComment, RecordFlag
from app.models.record_hold import RecordHold
from app.models.record_version import RecordVersion
from app.models.task import Task, TaskType

_LOT_RESULT_FIELDS = (
    "sample_rate", "sample_size", "acceptance_number", "accuracy_rate", "aql_status_snapshot",
    "qc_completed_at", "critical_defect_count", "minor_defect_count",
)


async def _lot_audit(db: AsyncSession, lot: Lot) -> list[AuditLog]:
    return list((await db.execute(
        select(AuditLog).where(
            AuditLog.tenant_id == lot.tenant_id,
            AuditLog.entity_type == AuditEntityType.lot,
            AuditLog.entity_id == lot.id,
        ).order_by(AuditLog.id)
    )).scalars().all())


def _is_release(row: AuditLog) -> bool:
    return row.action == AuditAction.status_changed and (row.new_value or {}).get("status") == "released"


async def _plan_lot(db: AsyncSession, lot: Lot) -> dict | None:
    """What resetting one lot changes, or None if it was never sampled.
    Audit ids are one global sequence, so the lot's first `sampled` event
    id marks "from the first sample onward"; task and batch ids work the
    same way from the lot's first QC task."""
    events = await _lot_audit(db, lot)
    # The customer's sample (apply_sample) — an outgoing inspection's draw is
    # also a `sampled` event, told apart by its inspection_id.
    sampled = [
        e for e in events
        if e.action == AuditAction.sampled and "inspection_id" not in (e.new_value or {})
    ]
    if not sampled:
        return None
    cutoff = sampled[0]
    record_ids = sorted((await db.execute(
        select(LotRecord.record_id).where(LotRecord.lot_id == lot.id)
    )).scalars().all())

    # Tasks: every task on the lot's records from the first QC task onward.
    first_qc_task = (await db.execute(
        select(Task.id).where(Task.record_id.in_(record_ids), Task.task_type == TaskType.qc)
        .order_by(Task.id).limit(1)
    )).scalar_one_or_none()
    tasks = []
    if first_qc_task is not None:
        tasks = list((await db.execute(
            select(Task).where(Task.record_id.in_(record_ids), Task.id >= first_qc_task)
        )).scalars().all())
    task_ids = sorted(t.id for t in tasks)

    # Restore each record to the batch of its last task before QC.
    records = list((await db.execute(select(Record).where(Record.id.in_(record_ids)))).scalars().all())
    prior_batch = {}
    for rid in record_ids:
        stmt = select(Task.batch_id).where(Task.record_id == rid)
        if first_qc_task is not None:
            stmt = stmt.where(Task.id < first_qc_task)
        prior_batch[rid] = (await db.execute(stmt.order_by(Task.id.desc()).limit(1))).scalar_one_or_none()

    # Batches: those of the deleted tasks, and any newer batch a record was
    # moved into, unless something not being reset still uses it.
    first_qc_batch = (await db.execute(
        select(Batch.id).where(Batch.id.in_({t.batch_id for t in tasks}), Batch.batch_type == BatchType.qc)
        .order_by(Batch.id).limit(1)
    )).scalar_one_or_none() if tasks else None
    candidates = {t.batch_id for t in tasks}
    if first_qc_batch is not None:
        candidates |= {r.batch_id for r in records if r.batch_id is not None and r.batch_id >= first_qc_batch}
    candidates -= set(prior_batch.values())
    batch_ids = []
    for bid in sorted(candidates):
        other_task = (await db.execute(
            select(Task.id).where(Task.batch_id == bid, Task.id.notin_(task_ids)).limit(1)
        )).first()
        other_record = (await db.execute(
            select(Record.id).where(Record.batch_id == bid, Record.id.notin_(record_ids)).limit(1)
        )).first()
        if not other_task and not other_record:
            batch_ids.append(bid)

    record_events = list((await db.execute(
        select(AuditLog).where(
            AuditLog.tenant_id == lot.tenant_id,
            AuditLog.entity_type == AuditEntityType.record,
            AuditLog.entity_id.in_(record_ids),
            AuditLog.id >= cutoff.id,
        )
    )).scalars().all())

    # Versions written from the first sample onward.
    first_new_version: dict[int, int] = {}
    for e in record_events:
        if e.action == AuditAction.version_created:
            n = (e.new_value or {}).get("version_number")
            if isinstance(n, int):
                first_new_version[e.entity_id] = min(n, first_new_version.get(e.entity_id, n))
    version_ids = []
    for rid, n in first_new_version.items():
        version_ids += (await db.execute(
            select(RecordVersion.id).where(RecordVersion.record_id == rid, RecordVersion.version_number >= n)
        )).scalars().all()

    flag_ids = {(e.new_value or {}).get("flag_id") for e in record_events if e.action == AuditAction.record_flagged}
    if batch_ids:
        flag_ids |= set((await db.execute(
            select(RecordFlag.id).where(RecordFlag.batch_id.in_(batch_ids))
        )).scalars().all())
    hold_ids = {(e.new_value or {}).get("hold_id") for e in record_events if e.action == AuditAction.record_held}

    # Resubmitted: put back the inspection the resubmit superseded and drop
    # the re-inspections made after it.
    restore_inspection = None
    inspection_ids = []
    resubmits = [e for e in events if e.action == AuditAction.resubmitted and e.id >= cutoff.id]
    if resubmits:
        restore_inspection = (resubmits[0].metadata_ or {}).get("superseded_inspection_id")
        if restore_inspection is not None:
            inspection_ids = list((await db.execute(
                select(LotInspection.id).where(LotInspection.lot_id == lot.id, LotInspection.id > restore_inspection)
            )).scalars().all())

    # The release the customer first sampled: a resubmit archives it in
    # round 1 (the live fields are then cleared, or hold a later release).
    release = lot.released_at, lot.released_by
    round_one = (await db.execute(
        select(LotQcRound).where(LotQcRound.lot_id == lot.id).order_by(LotQcRound.round_number).limit(1)
    )).scalar_one_or_none()
    if round_one is not None or lot.released_at is None:
        before = [e for e in events if _is_release(e) and e.id < cutoff.id]
        released_at = (round_one.released_at if round_one else None) or (
            before[-1].performed_at if before else datetime.now(timezone.utc)
        )
        release = released_at, before[-1].performed_by if before else lot.released_by

    deleted_tasks = {t.id: t for t in tasks}
    records_plan = []
    for r in records:
        had_rework = any(t.record_id == r.id and t.task_type != TaskType.qc for t in deleted_tasks.values())
        batch_id = prior_batch[r.id]
        if batch_id is None and r.batch_id is not None and r.batch_id not in batch_ids:
            batch_id = r.batch_id
        records_plan.append({
            "id": r.id, "status": r.status.value, "batch_id": r.batch_id, "new_batch_id": batch_id,
            "restore_data": had_rework or r.id in first_new_version,
        })

    return {
        "lot": lot, "status_before": lot.status.value, "qc_round_before": lot.qc_round,
        "cutoff_audit_id": cutoff.id, "record_ids": record_ids, "records": records_plan,
        "task_ids": task_ids, "batch_ids": batch_ids, "version_ids": sorted(version_ids),
        "flag_ids": sorted(i for i in flag_ids if isinstance(i, int)),
        "hold_ids": sorted(i for i in hold_ids if isinstance(i, int)),
        "restore_inspection_id": restore_inspection, "inspection_ids": sorted(inspection_ids),
        "release": release, "record_event_ids": [e.id for e in record_events],
        "lot_event_ids": [e.id for e in events if e.id >= cutoff.id],
    }


async def plan_reset(db: AsyncSession, project: Project) -> dict:
    lots = list((await db.execute(
        select(Lot).where(Lot.project_id == project.id).order_by(Lot.id)
    )).scalars().all())
    plans, skipped = [], []
    for lot in lots:
        plan = await _plan_lot(db, lot)
        if plan:
            plans.append(plan)
        elif lot.sample_size is not None or lot.status not in (LotStatus.draft, LotStatus.released):
            skipped.append(lot)  # sampled, but no audit trail to reset from

    lot_ids = {p["lot"].id for p in plans}
    task_ids = sorted({i for p in plans for i in p["task_ids"]})
    batch_ids = sorted({i for p in plans for i in p["batch_ids"]})
    audit_ids = {i for p in plans for i in p["record_event_ids"] + p["lot_event_ids"]}
    if task_ids:
        audit_ids |= set((await db.execute(select(AuditLog.id).where(
            AuditLog.entity_type == AuditEntityType.task, AuditLog.entity_id.in_(task_ids),
        ))).scalars().all())
    if batch_ids:
        audit_ids |= set((await db.execute(select(AuditLog.id).where(
            AuditLog.entity_type == AuditEntityType.batch, AuditLog.entity_id.in_(batch_ids),
        ))).scalars().all())

    # AQL switching events the QC verdicts caused.
    switches = [e for e in (await db.execute(select(AuditLog).where(
        AuditLog.entity_type == AuditEntityType.project, AuditLog.entity_id == project.id,
    ).order_by(AuditLog.id))).scalars().all() if (e.metadata_ or {}).get("lot_id") in lot_ids]
    audit_ids |= {e.id for e in switches}
    aql_status = (switches[0].old_value or {}).get("aql_status") if switches else None

    return {
        "project": project, "lots": plans, "skipped_lots": skipped,
        "task_ids": task_ids, "batch_ids": batch_ids, "audit_ids": sorted(audit_ids),
        "aql_status": aql_status, "reset_aql": bool(plans) and not skipped,
    }


async def apply_reset(db: AsyncSession, plan: dict) -> list[int]:
    """Apply a plan from plan_reset. Doesn't commit. Returns the ids of
    records whose pre-QC data couldn't be restored (no earlier version)."""
    lot_ids = [p["lot"].id for p in plan["lots"]]
    task_ids, batch_ids = plan["task_ids"], plan["batch_ids"]
    flag_ids = sorted({i for p in plan["lots"] for i in p["flag_ids"]})
    hold_ids = sorted({i for p in plan["lots"] for i in p["hold_ids"]})
    version_ids = sorted({i for p in plan["lots"] for i in p["version_ids"]})
    inspection_ids = sorted({i for p in plan["lots"] for i in p["inspection_ids"]})

    if plan["audit_ids"]:
        await db.execute(delete(AuditLog).where(AuditLog.id.in_(plan["audit_ids"])))
    if task_ids:
        await db.execute(delete(QcFieldResult).where(QcFieldResult.task_id.in_(task_ids)))
    await db.execute(delete(QcFieldResult).where(QcFieldResult.lot_id.in_(lot_ids)))
    await db.execute(delete(LotQcRound).where(LotQcRound.lot_id.in_(lot_ids)))
    if flag_ids:
        await db.execute(delete(Notification).where(Notification.flag_id.in_(flag_ids)))
        await db.execute(delete(FlagComment).where(FlagComment.flag_id.in_(flag_ids)))
        await db.execute(delete(RecordFlag).where(RecordFlag.id.in_(flag_ids)))
    if hold_ids:
        await db.execute(delete(RecordHold).where(RecordHold.id.in_(hold_ids)))
    if inspection_ids:
        items = select(LotInspectionItem.id).where(LotInspectionItem.inspection_id.in_(inspection_ids))
        await db.execute(delete(LotInspectionFieldResult).where(LotInspectionFieldResult.item_id.in_(items)))
        await db.execute(delete(LotInspectionItem).where(LotInspectionItem.inspection_id.in_(inspection_ids)))
        await db.execute(delete(LotInspection).where(LotInspection.id.in_(inspection_ids)))
    if version_ids:
        await db.execute(delete(RecordVersion).where(RecordVersion.id.in_(version_ids)))

    not_restored = []
    for p in plan["lots"]:
        lot = p["lot"]
        for field in _LOT_RESULT_FIELDS:
            setattr(lot, field, None)
        lot.qc_round = 1
        lot.status = LotStatus.released
        lot.released_at, lot.released_by = p["release"]
        if p["restore_inspection_id"] is not None:
            inspection = await db.get(LotInspection, p["restore_inspection_id"])
            if inspection is not None and inspection.status == LotInspectionStatus.superseded:
                inspection.status = LotInspectionStatus.passed
        for lr in (await db.execute(select(LotRecord).where(LotRecord.lot_id == lot.id))).scalars():
            lr.is_sampled = False

        for rp in p["records"]:
            record = await db.get(Record, rp["id"])
            record.status = RecordStatus.qa_passed
            record.locked_by = None
            record.locked_at = None
            record.batch_id = rp["new_batch_id"]
            if rp["restore_data"]:
                last = (await db.execute(
                    select(RecordVersion).where(RecordVersion.record_id == record.id)
                    .order_by(RecordVersion.version_number.desc()).limit(1)
                )).scalar_one_or_none()
                if last is not None:
                    record.indexed_data = last.indexed_data
                    record.current_version = last.version_number + 1
                else:
                    record.current_version = 1
                    not_restored.append(record.id)
    await db.flush()

    if batch_ids:
        await db.execute(delete(BatchQCResult).where(BatchQCResult.batch_id.in_(batch_ids)))
    if task_ids:
        await db.execute(delete(Task).where(Task.id.in_(task_ids)))
    if batch_ids:
        await db.execute(delete(Batch).where(Batch.id.in_(batch_ids)))

    if plan["reset_aql"]:
        config = (await db.execute(
            select(AQLConfig).where(AQLConfig.project_id == plan["project"].id)
        )).scalar_one_or_none()
        if config is not None:
            if plan["aql_status"]:
                config.current_status = AQLStatus(plan["aql_status"])
            config.consecutive_passes = 0
            config.consecutive_failures = 0
    await db.flush()
    return sorted(not_restored)


def _print_plan(plan: dict) -> None:
    project = plan["project"]
    print(f"Project: {project.name!r} (id={project.id}, tenant_id={project.tenant_id})\n")
    if not plan["lots"]:
        print("No lot has been sampled by the customer — nothing to reset.")
    for p in plan["lots"]:
        lot = p["lot"]
        by_status: dict[str, int] = {}
        for r in p["records"]:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        print(f"Lot {lot.id} {lot.name!r}: {p['status_before']} (round {p['qc_round_before']}) → released, unsampled")
        print(f"  records: {len(p['records'])}  now {by_status} → all qa_passed")
        print(f"  tasks deleted: {len(p['task_ids'])}   batches deleted: {len(p['batch_ids'])}")
        print(f"  rework versions deleted: {len(p['version_ids'])}   "
              f"data restored for: {sum(r['restore_data'] for r in p['records'])} record(s)")
        if p["flag_ids"] or p["hold_ids"]:
            print(f"  flags deleted: {len(p['flag_ids'])}   holds deleted: {len(p['hold_ids'])}")
        if p["restore_inspection_id"] is not None:
            print(f"  inspection {p['restore_inspection_id']} back to passed; "
                  f"re-inspections deleted: {p['inspection_ids']}")
    print(f"\nAudit log entries deleted: {len(plan['audit_ids'])}")
    if plan["reset_aql"]:
        status = plan["aql_status"] or "unchanged"
        print(f"AQL switching: status {status}, consecutive passes/failures reset to 0")
    for lot in plan["skipped_lots"]:
        print(f"WARNING: lot {lot.id} {lot.name!r} ({lot.status.value}) has QC results but no "
              f"'sampled' audit event — left alone, and the AQL switching state is not reset.")


def _report(plan: dict, *, dry_run: bool, not_restored: list[int]) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "project": {"id": plan["project"].id, "name": plan["project"].name},
        "lots": [{
            "id": p["lot"].id, "name": p["lot"].name, "status_before": p["status_before"],
            "qc_round_before": p["qc_round_before"], "cutoff_audit_id": p["cutoff_audit_id"],
            "records": p["records"], "task_ids": p["task_ids"], "batch_ids": p["batch_ids"],
            "version_ids": p["version_ids"], "flag_ids": p["flag_ids"], "hold_ids": p["hold_ids"],
            "restored_inspection_id": p["restore_inspection_id"], "deleted_inspection_ids": p["inspection_ids"],
        } for p in plan["lots"]],
        "skipped_lot_ids": [lot.id for lot in plan["skipped_lots"]],
        "audit_log_ids_deleted": plan["audit_ids"],
        "aql_reset": plan["reset_aql"], "aql_status": plan["aql_status"],
        "records_data_not_restored": not_restored,
    }


async def _choose_project(db: AsyncSession, args) -> Project:
    if args.project_id is not None:
        project = await db.get(Project, args.project_id)
        if not project:
            sys.exit(f"ERROR: no project with id={args.project_id}")
        return project
    name = args.project_name
    if name is None:
        for p in (await db.execute(select(Project).order_by(Project.id))).scalars():
            print(f"{p.id:>5}  {p.name}")
        name = input("\nTraining project (name or id): ").strip()
        if name.isdigit():
            project = await db.get(Project, int(name))
            if not project:
                sys.exit(f"ERROR: no project with id={name}")
            return project
    matches = list((await db.execute(select(Project).where(Project.name == name))).scalars().all())
    if len(matches) != 1:
        sys.exit(f"ERROR: {len(matches)} projects named {name!r} — use --project-id")
    return matches[0]


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--project-id", type=int)
    target.add_argument("--project-name")
    parser.add_argument("--confirm", action="store_true", help="Apply. Without it, dry run only.")
    parser.add_argument("--yes", action="store_true", help="Skip the retype-the-name prompt (needs --confirm).")
    parser.add_argument("--report", type=Path, help="Where to write the JSON report.")
    args = parser.parse_args(argv)

    async with AsyncSessionLocal() as db:
        project = await _choose_project(db, args)
        plan = await plan_reset(db, project)
        _print_plan(plan)

        if not args.confirm:
            print("\nDry run only — no changes made. Re-run with --confirm to apply.")
            if args.report:
                args.report.write_text(json.dumps(_report(plan, dry_run=True, not_restored=[]), indent=2, default=str))
                print(f"Dry-run report written to {args.report}")
            return 0
        if not plan["lots"]:
            return 0
        if not args.yes:
            typed = input(f"\nType the project name exactly to confirm ({project.name!r}): ")
            if typed != project.name:
                print("ERROR: name did not match — no changes made.", file=sys.stderr)
                return 2

        not_restored = await apply_reset(db, plan)
        await db.commit()
        print(f"\nReset {len(plan['lots'])} lot(s).")
        if not_restored:
            print(f"NOTE: {len(not_restored)} reworked record(s) had no version from before QC, so they keep "
                  f"their reworked data: {not_restored}")
        path = args.report or Path(
            f"reset_training_qc_project{project.id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        path.write_text(json.dumps(_report(plan, dry_run=False, not_restored=not_restored), indent=2, default=str))
        print(f"Report written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
