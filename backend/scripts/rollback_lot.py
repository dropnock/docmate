"""
Rolls back the creation of a single lot — deletes its Lot and LotRecord
rows. Only handles a lot that's still in a side-effect-free state:

  draft     — create_lot only wrote Lot + LotRecord rows; no record status
              changed, no tasks/batches created.
  released  — release_lot only flips lot.status/released_at/released_by;
              still no record/task/batch changes.

Refuses (no --force) once a lot has been sampled or has QC batches, since
apply_sample moves sampled records to qc_pending and create_qc_batches can
spin up real QC Batch/Task rows a customer QC agent may already be working —
rolling that back means reverting record statuses and tearing down that
work too, which this script deliberately does not attempt blind. Handle
that case by hand once you've confirmed no real QC work has happened yet.

lot_service writes its own audit events under entity_type=record (there's
no AuditEntityType.lot — see lot_service.py; same reused-entity_type quirk
cabinet_service.py has for AuditEntityType.record/entity_id=cabinet.id), so
entity_id=<lot_id> can collide with a genuine Record or Cabinet that happens
to share the same numeric id. To avoid deleting a real record's audit
history by mistake, this script only deletes an entity_type=record row
matching entity_id=<lot_id> if its action/value shape is exactly what
lot_service.create_lot/release_lot write (a real record's own "created"
event always carries a "source" key instead — see cabinet_service.
ingest_json_records/upload_scan — and a cabinet's carries "project_id").
Anything that doesn't match that shape is left alone and reported, not
guessed at.

Deleting audit_logs means the app's own audit trail can't tell you this lot
ever existed afterward, so a --confirm run always writes a JSON report of
every id it touched (auto-named if --report isn't given) — that report is
the only record of what was removed.

Dry-run by default: prints exactly what would be deleted and makes no
changes. Pass --confirm to actually execute.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.rollback_lot --lot-id 1                # dry run
    docker compose exec backend python -m scripts.rollback_lot --lot-id 1 --confirm
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
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.lot import Lot, LotRecord, LotStatus

SAFE_STATUSES = (LotStatus.draft, LotStatus.released)


async def _resolve_lot(db: AsyncSession, lot_id: int) -> Lot:
    lot = await db.get(Lot, lot_id)
    if not lot:
        sys.exit(f"ERROR: no lot with id={lot_id}")
    if lot.status not in SAFE_STATUSES:
        sys.exit(
            f"ERROR: lot {lot_id} has status={lot.status.value!r} — this script only rolls back "
            f"draft/released lots (no sampling or QC batches yet). Sampled/qc_in_progress/passed/"
            f"failed/remediation lots need manual review since real QC work may already exist."
        )
    return lot


def _is_lot_created_event(row: AuditLog, lot: Lot) -> bool:
    nv = row.new_value or {}
    return row.action == AuditAction.created and "record_count" in nv and nv.get("name") == lot.name


def _is_lot_released_event(row: AuditLog, lot: Lot) -> bool:
    return (
        row.action == AuditAction.status_changed
        and (row.old_value or {}).get("status") == "draft"
        and (row.new_value or {}).get("status") == "released"
    )


async def _find_lot_audit_rows(db: AsyncSession, lot: Lot) -> tuple[list[AuditLog], list[AuditLog]]:
    """Returns (matched, skipped) — skipped rows share entity_id with this lot
    (entity_type=record) but don't match lot_service's known event shapes, so
    they're left untouched rather than assumed to be ours."""
    candidates = list((await db.execute(
        select(AuditLog).where(
            AuditLog.tenant_id == lot.tenant_id,
            AuditLog.entity_type == AuditEntityType.record,
            AuditLog.entity_id == lot.id,
            AuditLog.action.in_([AuditAction.created, AuditAction.status_changed]),
        )
    )).scalars().all())

    matched, skipped = [], []
    for row in candidates:
        if _is_lot_created_event(row, lot) or _is_lot_released_event(row, lot):
            matched.append(row)
        else:
            skipped.append(row)
    return matched, skipped


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--lot-id", required=True, type=int)
    parser.add_argument(
        "--confirm", action="store_true", help="Actually delete. Without this flag, dry run only."
    )
    parser.add_argument(
        "--report", type=Path, default=None, help="Where to write the JSON report (auto-named if omitted)."
    )
    args = parser.parse_args(argv)

    async with AsyncSessionLocal() as db:
        lot = await _resolve_lot(db, args.lot_id)

        record_ids = list(
            (await db.execute(select(LotRecord.record_id).where(LotRecord.lot_id == lot.id))).scalars().all()
        )
        matched_audit, skipped_audit = await _find_lot_audit_rows(db, lot)

        print(f"Lot: {lot.name!r} (id={lot.id}, tenant_id={lot.tenant_id}, status={lot.status.value})")
        print(f"LotRecord rows to delete: {len(record_ids)}   (underlying records are left untouched)")
        print(f"  Record ids: {record_ids}")
        print(f"Audit log entries to delete: {len(matched_audit)}   ids: {[r.id for r in matched_audit]}")
        if skipped_audit:
            print(
                f"  NOTE: {len(skipped_audit)} audit_logs row(s) share entity_id={lot.id} under "
                f"entity_type=record but don't match lot_service's event shape — left untouched: "
                f"{[r.id for r in skipped_audit]}"
            )

        if not args.confirm:
            print("\nDry run only — no changes made. Re-run with --confirm to apply.")
            if args.report:
                _write_report(args.report, lot, record_ids, matched_audit, skipped_audit, dry_run=True)
                print(f"Dry-run report written to {args.report}")
            return 0

        await db.execute(delete(AuditLog).where(AuditLog.id.in_([r.id for r in matched_audit])))
        await db.execute(delete(LotRecord).where(LotRecord.lot_id == lot.id))
        await db.execute(delete(Lot).where(Lot.id == lot.id))
        await db.commit()
        print(f"\nRolled back lot {lot.id} ({lot.name!r}).")

        report_path = args.report or Path(
            f"rollback_lot_{lot.id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        _write_report(report_path, lot, record_ids, matched_audit, skipped_audit, dry_run=False)
        print(f"Report written to {report_path}")

    return 0


def _write_report(
    path: Path, lot: Lot, record_ids: list[int], matched_audit: list[AuditLog],
    skipped_audit: list[AuditLog], *, dry_run: bool,
) -> None:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "lot": {"id": lot.id, "name": lot.name, "tenant_id": lot.tenant_id, "status": lot.status.value},
        "lot_record_ids_deleted": record_ids,
        "audit_log_ids_deleted": [r.id for r in matched_audit],
        "audit_log_ids_skipped": [r.id for r in skipped_audit],
    }
    path.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
