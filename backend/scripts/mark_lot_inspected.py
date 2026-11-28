"""
Mark a draft lot as inspected, so the supervisor can release it without
reviewing the outgoing ISO 2859-1 sample by hand (e.g. in training).

It runs the normal outgoing inspection through lot_inspection_service:
start an attempt at the chosen inspection level (the sample is drawn and its
seed stored as usual), mark every field of every sampled record accepted, and
complete it, so it passes. Nothing is released: the lot stays in draft, and
the supervisor releases it from the Lots page as usual.

The attempt is recorded under the inspector you name (--inspector, a
digitizing supervisor or admin of the lot's tenant), and its summary note
says the script marked it and no fields were actually reviewed (override
with --note). The audit trail shows the usual sampled / passed events.

  - All lot records must be qa_passed (as for any inspection). If some are
    still in rework, the script lists them and changes nothing.
  - A lot whose latest attempt already passed is left as it is.
  - An attempt already in progress is finished: its pending records are
    marked accepted. If the supervisor has already marked a defect in it,
    the script stops, since completing it would fail the lot and requeue
    those records. Pass --restart to cancel that attempt and start a fresh one.

Dry run by default: shows what it would do. Pass --confirm to apply.

    docker compose exec backend python -m scripts.mark_lot_inspected                  # lists draft lots
    docker compose exec backend python -m scripts.mark_lot_inspected --lot-id 7 --inspector sup@doc.local
    docker compose exec backend python -m scripts.mark_lot_inspected --lot-id 7 --inspector sup@doc.local --confirm
    docker compose exec backend python -m scripts.mark_lot_inspected --lot-id 7 --inspector sup@doc.local \\
        --level tightened --confirm
"""
import argparse
import asyncio
import sys

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.aql import AQLStatus
from app.models.lot import Lot, LotRecord, LotStatus
from app.models.lot_inspection import InspectionItemStatus, LotInspection, LotInspectionItem, LotInspectionStatus
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.models.user import User, UserRole
from app.schemas.task import FieldResultIn
from app.services import aql_service, lot_inspection_service, review_fields

DEFAULT_NOTE = "Marked inspected by scripts.mark_lot_inspected: every sampled field accepted, none actually reviewed."
INSPECTOR_ROLES = (UserRole.de_supervisor, UserRole.admin)


class Refused(Exception):
    """The lot can't be marked inspected as asked; nothing was changed."""


async def _inspections(db: AsyncSession, lot_id: int) -> list[LotInspection]:
    return list((await db.execute(
        select(LotInspection).where(LotInspection.lot_id == lot_id).order_by(LotInspection.attempt_number)
    )).scalars().all())


async def _items(db: AsyncSession, inspection_id: int) -> list[LotInspectionItem]:
    return list((await db.execute(
        select(LotInspectionItem).where(LotInspectionItem.inspection_id == inspection_id)
        .order_by(LotInspectionItem.record_id)
    )).scalars().all())


async def _accept_all(db: AsyncSession, item: LotInspectionItem) -> list[FieldResultIn]:
    schema = await lot_inspection_service._schema(db, item.document_type_id)
    record = await db.get(Record, item.record_id)
    return [
        FieldResultIn(field_key=f["key"], status="accepted")
        for f in review_fields.review_fields(schema, record.indexed_data)
    ]


async def plan(db: AsyncSession, lot: Lot, *, restart: bool) -> dict:
    """What marking this lot inspected involves. Raises Refused when it
    can't be done; returns {"action": "none"} when it already passed."""
    if lot.status != LotStatus.draft:
        raise Refused(f"lot {lot.id} is {lot.status.value} — only a draft lot is inspected before release")
    not_ready = sorted((await db.execute(
        select(Record.id).join(LotRecord, LotRecord.record_id == Record.id)
        .where(LotRecord.lot_id == lot.id, Record.status != RecordStatus.qa_passed)
    )).scalars().all())
    if not_ready:
        raise Refused(
            f"{len(not_ready)} record(s) in lot {lot.id} are not qa_passed (still in rework): {not_ready[:50]}"
        )

    inspections = await _inspections(db, lot.id)
    latest = lot_inspection_service._latest_effective(inspections)
    if latest is not None and latest.status == LotInspectionStatus.passed:
        return {"action": "none", "inspection": latest}

    in_progress = next((i for i in inspections if i.status == LotInspectionStatus.in_progress), None)
    if in_progress is not None:
        items = await _items(db, in_progress.id)
        defective = [i.record_id for i in items if i.status == InspectionItemStatus.defective]
        if defective and not restart:
            raise Refused(
                f"inspection #{in_progress.attempt_number} of lot {lot.id} is in progress and has defects marked "
                f"on record(s) {defective} — completing it would fail the lot. Pass --restart to cancel it and "
                f"start a fresh attempt."
            )
        if not defective:
            pending = [i for i in items if i.status == InspectionItemStatus.pending]
            return {"action": "finish", "inspection": in_progress, "pending": len(pending), "sample": len(items)}
        return {"action": "restart", "inspection": in_progress}
    return {"action": "start"}


async def mark_inspected(
    db: AsyncSession, lot: Lot, *, inspector: User, level: AQLStatus | None, note: str, restart: bool,
) -> LotInspection:
    """Apply it. Doesn't commit."""
    p = await plan(db, lot, restart=restart)
    if p["action"] == "none":
        return p["inspection"]
    tenant_id = lot.tenant_id
    if p["action"] == "restart":
        await lot_inspection_service.cancel_inspection(
            db, lot_id=lot.id, inspection_id=p["inspection"].id, user_id=inspector.id, tenant_id=tenant_id,
        )
    if p["action"] in ("start", "restart"):
        chosen = level or await aql_service.get_suggested_level(db, lot.project_id)
        inspection = await lot_inspection_service.start_inspection(
            db, lot_id=lot.id, inspection_level=chosen, user_id=inspector.id, tenant_id=tenant_id,
        )
    else:
        inspection = p["inspection"]

    for item in await _items(db, inspection.id):
        if item.status == InspectionItemStatus.pending:
            await lot_inspection_service.review_item(
                db, lot_id=lot.id, inspection_id=inspection.id, record_id=item.record_id,
                field_results=await _accept_all(db, item), tenant_id=tenant_id,
            )
    inspection = await lot_inspection_service.complete_inspection(
        db, lot_id=lot.id, inspection_id=inspection.id, summary_note=note,
        user_id=inspector.id, tenant_id=tenant_id,
    )
    if inspection.status != LotInspectionStatus.passed:  # can't happen with every field accepted
        raise Refused(f"inspection #{inspection.attempt_number} did not pass ({inspection.status.value})")
    await lot_inspection_service.assert_releasable(db, lot)
    return inspection


async def _find_inspector(db: AsyncSession, lot: Lot, who: str) -> User:
    stmt = select(User).where(User.tenant_id == lot.tenant_id)
    stmt = stmt.where(User.id == int(who)) if who.isdigit() else stmt.where(func.lower(User.email) == who.lower())
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise Refused(f"no user {who!r} in lot {lot.id}'s tenant")
    if user.role not in INSPECTOR_ROLES or not user.is_active:
        raise Refused(f"{user.email} is {user.role.value}{'' if user.is_active else ', inactive'} — "
                      f"the inspector must be an active digitizing supervisor or admin")
    return user


async def _print_draft_lots(db: AsyncSession) -> None:
    rows = (await db.execute(
        select(Lot, Project.name).join(Project, Project.id == Lot.project_id)
        .where(Lot.status == LotStatus.draft).order_by(Lot.id)
    )).all()
    if not rows:
        print("No draft lots.")
        return
    print(f"{'lot':>5}  {'records':>7}  {'inspection':<12}  project / lot")
    for lot, project_name in rows:
        count = (await db.execute(
            select(func.count()).select_from(LotRecord).where(LotRecord.lot_id == lot.id)
        )).scalar_one()
        latest = lot_inspection_service._latest_effective(await _inspections(db, lot.id))
        state = latest.status.value if latest else "none"
        print(f"{lot.id:>5}  {count:>7}  {state:<12}  {project_name} / {lot.name}")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lot-id", type=int, help="Lot to mark inspected (omit to list draft lots).")
    parser.add_argument("--inspector", help="Email or id of the digitizing supervisor/admin to record as inspector.")
    parser.add_argument("--level", choices=[s.value for s in AQLStatus],
                        help="Inspection level (default: the project's suggested level).")
    parser.add_argument("--note", default=DEFAULT_NOTE, help="Summary note stored on the inspection.")
    parser.add_argument("--restart", action="store_true",
                        help="Cancel an in-progress attempt that has defects marked, and start afresh.")
    parser.add_argument("--confirm", action="store_true", help="Apply. Without it, dry run only.")
    args = parser.parse_args(argv)

    async with AsyncSessionLocal() as db:
        if args.lot_id is None:
            await _print_draft_lots(db)
            return 0
        lot = await db.get(Lot, args.lot_id)
        if lot is None:
            print(f"ERROR: no lot with id={args.lot_id}", file=sys.stderr)
            return 1
        if not args.inspector:
            print("ERROR: --inspector is required (email or id of a digitizing supervisor)", file=sys.stderr)
            return 1
        try:
            inspector = await _find_inspector(db, lot, args.inspector)
            p = await plan(db, lot, restart=args.restart)
        except Refused as exc:
            print(f"ERROR: {exc}. No changes made.", file=sys.stderr)
            return 2

        count = (await db.execute(
            select(func.count()).select_from(LotRecord).where(LotRecord.lot_id == lot.id)
        )).scalar_one()
        print(f"Lot {lot.id} {lot.name!r}: draft, {count} record(s), all qa_passed")
        if p["action"] == "none":
            print(f"Already passed inspection #{p['inspection'].attempt_number} — ready to release. Nothing to do.")
            return 0
        level = args.level or (await aql_service.get_suggested_level(db, lot.project_id)).value
        if p["action"] == "finish":
            print(f"Will finish in-progress inspection #{p['inspection'].attempt_number}: accept the "
                  f"{p['pending']} of {p['sample']} sampled record(s) not yet reviewed, then complete it.")
        else:
            if p["action"] == "restart":
                print(f"Will cancel in-progress inspection #{p['inspection'].attempt_number} (it has defects marked).")
            print(f"Will start a new inspection at level {level!r}, accept every sampled record, and complete it.")
        print(f"Inspector: {inspector.full_name} <{inspector.email}>")

        if not args.confirm:
            print("\nDry run only — no changes made. Re-run with --confirm to apply.")
            return 0
        try:
            inspection = await mark_inspected(
                db, lot, inspector=inspector, level=AQLStatus(level), note=args.note, restart=args.restart,
            )
        except (Refused, HTTPException) as exc:
            await db.rollback()
            detail = exc.detail if isinstance(exc, HTTPException) else exc
            print(f"ERROR: {detail}. No changes made.", file=sys.stderr)
            return 2
        await db.commit()
        print(f"\nInspection #{inspection.attempt_number} passed: sample {inspection.sample_size} of "
              f"{inspection.lot_size}, Ac {inspection.acceptance_number}, level {inspection.inspection_level.value}.")
        print("The lot is ready to release from the Lots page.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
