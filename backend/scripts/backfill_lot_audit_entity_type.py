"""
One-time backfill: retypes audit events that lot_service and
cabinet_service used to write under entity_type=record with a lot or
cabinet id as entity_id. Those rows showed up in the history of whichever
record shared the id (including in the customer's Record History), and
were missing from the lot's own history.

Only the entity_type column changes; every other field, including
entity_id, is left as written. A row is retyped only when its action and
value shape are exactly what the old code wrote. A real record's own
events never have these shapes: a record's "created" event always carries
a "source" key, and records never use the "sampled" action or the
draft/released and failed/remediation statuses.

  -> lot      created         new_value keys == {name, record_count}
  -> lot      sampled         (any; only lot_service.apply_sample wrote it)
  -> lot      status_changed  draft -> released, failed -> remediation,
                              or new_value has accuracy_rate
  -> cabinet  created         new_value keys == {name, project_id}

Dry-run by default: prints what would change and makes no changes. Pass
--confirm to apply. Needs migration d7e8f9a0b1c2 (adds 'cabinet') first.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.backfill_lot_audit_entity_type
    docker compose exec backend python -m scripts.backfill_lot_audit_entity_type --confirm
    docker compose exec backend python -m scripts.backfill_lot_audit_entity_type --tenant-id 3 --confirm
"""
import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog

MAX_IDS_TO_PRINT = 50

_LOT_STATUS_TRANSITIONS = {("draft", "released"), ("failed", "remediation")}


def _target_type(row: AuditLog) -> AuditEntityType | None:
    """The entity type this record-typed row should have, or None if it's a
    genuine record event (or a shape we don't recognise) and must be left alone."""
    nv = row.new_value or {}
    ov = row.old_value or {}
    if row.action == AuditAction.created:
        keys = set(nv)
        if keys == {"name", "record_count"}:
            return AuditEntityType.lot
        if keys == {"name", "project_id"}:
            return AuditEntityType.cabinet
        return None
    if row.action == AuditAction.sampled:
        return AuditEntityType.lot
    if row.action == AuditAction.status_changed:
        if (ov.get("status"), nv.get("status")) in _LOT_STATUS_TRANSITIONS or "accuracy_rate" in nv:
            return AuditEntityType.lot
    return None


async def _find_misfiled(
    db: AsyncSession, *, tenant_id: int | None
) -> list[tuple[AuditLog, AuditEntityType]]:
    stmt = select(AuditLog).where(
        AuditLog.entity_type == AuditEntityType.record,
        AuditLog.action.in_([AuditAction.created, AuditAction.sampled, AuditAction.status_changed]),
    )
    if tenant_id is not None:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    rows = (await db.execute(stmt.order_by(AuditLog.id))).scalars().all()
    out = []
    for row in rows:
        target = _target_type(row)
        if target is not None:
            out.append((row, target))
    return out


async def _apply_fix(db: AsyncSession, misfiled: list[tuple[AuditLog, AuditEntityType]]) -> None:
    for row, target in misfiled:
        row.entity_type = target


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tenant-id", type=int, default=None)
    parser.add_argument("--confirm", action="store_true", help="Apply. Without this flag, dry run only.")
    args = parser.parse_args(argv)

    async with AsyncSessionLocal() as db:
        misfiled = await _find_misfiled(db, tenant_id=args.tenant_id)
        counts = Counter((t.value, r.action.value) for r, t in misfiled)
        print(f"Misfiled audit rows found: {len(misfiled)}")
        for (target, action), n in sorted(counts.items()):
            print(f"  record -> {target:8} {action:15} {n}")
        ids = [r.id for r, _ in misfiled]
        if ids:
            shown = ids[:MAX_IDS_TO_PRINT]
            more = f" … (+{len(ids) - len(shown)} more)" if len(ids) > len(shown) else ""
            print(f"  audit_log ids: {shown}{more}")

        if not args.confirm:
            print("\nDry run only — no changes made. Re-run with --confirm to apply.")
            return 0

        await _apply_fix(db, misfiled)
        await db.commit()
        print(f"\nRetyped {len(misfiled)} audit row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
