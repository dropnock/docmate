import math
import random
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import SamplingMode
from app.models.audit_log import AuditAction, AuditEntityType
from app.models.batch import Batch, BatchStatus, BatchType
from app.models.lot import Lot, LotRecord, LotStatus
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.services import audit_service, aql_service, lot_inspection_service


async def _get_lot(db: AsyncSession, lot_id: int, tenant_id: int) -> Lot:
    lot = await db.get(Lot, lot_id)
    if not lot or lot.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot


async def _get_lot_for_update(db: AsyncSession, lot_id: int, tenant_id: int) -> Lot:
    """_get_lot with the row locked (SELECT ... FOR UPDATE) until the caller's
    transaction ends. recall_lot and apply_sample both take it, so a recall
    and the customer's sampling can't both succeed on the same released lot.
    populate_existing refreshes a copy already in the session."""
    lot = (await db.execute(
        select(Lot).where(Lot.id == lot_id).with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if not lot or lot.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot


async def create_lot(
    db: AsyncSession,
    *,
    project_id: int,
    name: str,
    description: str | None,
    record_ids: list[int],
    user_id: int,
    tenant_id: int,
) -> Lot:
    from sqlalchemy.exc import IntegrityError
    from app.services.s3_service import resolve_record_project

    record_ids = list(dict.fromkeys(record_ids))  # drop repeats, keep order
    if not record_ids:
        raise HTTPException(status_code=422, detail="A lot needs at least one record")
    for rid in record_ids:
        record = await db.get(Record, rid)
        if not record:
            raise HTTPException(status_code=404, detail=f"Record {rid} not found")
        project = await resolve_record_project(record, db)
        if project is None or project.id != project_id:
            raise HTTPException(status_code=422, detail=f"Record {rid} does not belong to this project")
        if record.status != RecordStatus.qa_passed:
            raise HTTPException(
                status_code=422,
                detail=f"Record {rid} is not qa_passed (status={record.status})",
            )
    # A record belongs to at most one lot (also enforced by the unique index
    # on lot_records.record_id). Removing it from a draft lot frees it.
    taken = (await db.execute(
        select(LotRecord.record_id, LotRecord.lot_id).where(LotRecord.record_id.in_(record_ids))
    )).all()
    if taken:
        raise HTTPException(
            status_code=409,
            detail="Records already in a lot: " + ", ".join(f"{r} (lot {l})" for r, l in sorted(taken)),
        )

    lot = Lot(
        tenant_id=tenant_id,
        project_id=project_id,
        name=name,
        description=description,
        status=LotStatus.draft,
        created_by=user_id,
    )
    db.add(lot)
    await db.flush()

    for rid in record_ids:
        db.add(LotRecord(lot_id=lot.id, record_id=rid, is_sampled=False))

    try:
        await db.flush()
    except IntegrityError:
        # Another request put one of these records into a lot between the
        # check above and this insert.
        raise HTTPException(status_code=409, detail="One or more records were just added to another lot")
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.lot,
        entity_id=lot.id,
        action=AuditAction.created,
        performed_by=user_id,
        new_value={"name": name, "record_count": len(record_ids)},
    )
    return lot


async def list_lots(
    db: AsyncSession,
    *,
    project_id: int,
    tenant_id: int,
    exclude_drafts: bool = False,
) -> list[Lot]:
    """exclude_drafts=True is the customer's view: a draft lot hasn't been
    handed over (or was recalled), so it doesn't exist as far as they know."""
    stmt = select(Lot).where(Lot.project_id == project_id, Lot.tenant_id == tenant_id)
    if exclude_drafts:
        stmt = stmt.where(Lot.status != LotStatus.draft)
    result = await db.execute(stmt.order_by(Lot.created_at.desc()))
    return list(result.scalars().all())


async def release_lot(
    db: AsyncSession,
    *,
    lot_id: int,
    user_id: int,
    tenant_id: int,
) -> Lot:
    lot = await _get_lot(db, lot_id, tenant_id)
    if lot.status != LotStatus.draft:
        raise HTTPException(status_code=409, detail="Lot must be in draft to release")
    await lot_inspection_service.assert_releasable(db, lot)
    lot.status = LotStatus.released
    lot.released_at = datetime.now(timezone.utc)
    lot.released_by = user_id
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.lot,
        entity_id=lot.id,
        action=AuditAction.status_changed,
        performed_by=user_id,
        old_value={"status": "draft"},
        new_value={"status": "released"},
    )
    return lot


async def apply_sample(
    db: AsyncSession,
    *,
    lot_id: int,
    sample_rate: float | None,
    user_id: int,
    tenant_id: int,
) -> Lot:
    lot = await _get_lot_for_update(db, lot_id, tenant_id)
    if lot.status != LotStatus.released:
        raise HTTPException(status_code=409, detail="Lot must be released before sampling")

    config = await aql_service.get_config(db, lot.project_id)
    # No AQLConfig row for the project (shouldn't happen in practice —
    # create_project always provisions one — but degrade the same way
    # get_current_aql_level does rather than erroring) -> today's only
    # behavior, manual rate.
    mode = config.sampling_mode if config else SamplingMode.manual

    lot_records_result = await db.execute(
        select(LotRecord).where(LotRecord.lot_id == lot_id)
    )
    all_lot_records = list(lot_records_result.scalars().all())
    total = len(all_lot_records)

    acceptance_number: int | None = None
    if mode == SamplingMode.iso:
        if sample_rate is not None:
            raise HTTPException(
                status_code=422,
                detail="sample_rate must not be supplied when sampling_mode is 'iso' — the sample size is computed from the ISO 2859-1 table",
            )
        aql_level = await aql_service.get_current_aql_level(db, lot.project_id)
        computed_size, acceptance_number = aql_service.compute_sample_size(total, aql_level)
        sample_size = min(computed_size, total) if total else 0
        derived_rate = sample_size / total if total else 0.0
    else:
        if sample_rate is None or not 0 < sample_rate <= 1:
            raise HTTPException(
                status_code=422,
                detail="sample_rate is required and must be between 0 and 1 when sampling_mode is 'manual'",
            )
        sample_size = max(1, math.ceil(sample_rate * total))
        derived_rate = sample_rate

    sampled = random.sample(all_lot_records, min(sample_size, total))

    for lr in sampled:
        lr.is_sampled = True
        record = await db.get(Record, lr.record_id)
        if record:
            record.status = RecordStatus.qc_pending

    lot.sample_rate = derived_rate
    lot.sample_size = sample_size
    lot.acceptance_number = acceptance_number
    lot.aql_status_snapshot = config.current_status if config else None
    lot.status = LotStatus.qc_in_progress

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.lot,
        entity_id=lot.id,
        action=AuditAction.sampled,
        performed_by=user_id,
        new_value={
            "sampling_mode": mode.value,
            "sample_rate": derived_rate,
            "sample_size": sample_size,
            "acceptance_number": acceptance_number,
            "total": total,
        },
    )
    return lot


async def create_qc_batches(
    db: AsyncSession,
    *,
    lot_id: int,
    project_id: int,
    document_type_id: int,
    assignments: list[dict],  # [{agent_id, record_ids}]
    supervisor_id: int,
    tenant_id: int,
) -> list[Batch]:
    lot = await _get_lot(db, lot_id, tenant_id)
    if lot.status != LotStatus.qc_in_progress:
        raise HTTPException(status_code=409, detail="Lot must be in qc_in_progress to create QC batches")
    await _check_qc_batch_targets(
        db, lot=lot, project_id=project_id, document_type_id=document_type_id,
        agent_ids=[a["agent_id"] for a in assignments], tenant_id=tenant_id,
    )

    if not assignments:
        raise HTTPException(status_code=400, detail="assignments must not be empty")
    if any(not a["record_ids"] for a in assignments):
        raise HTTPException(status_code=400, detail="each assignment must include at least one record")
    all_record_ids = [rid for a in assignments for rid in a["record_ids"]]
    if len(all_record_ids) != len(set(all_record_ids)):
        raise HTTPException(status_code=400, detail="a record cannot be assigned to more than one QC batch in the same request")

    # A record is only eligible for a new QC batch if it belongs to this lot,
    # is sampled for QC (qc_pending — record.status stays qc_pending for the
    # whole time it's being worked, see task_service.start_task), and has no
    # existing pending/in-progress QC task — otherwise the same record could
    # be silently double-assigned across separate qc-batch requests, the same
    # class of bug fixed for create_indexing_batch above.
    eligible_result = await db.execute(
        select(Record.id)
        .join(LotRecord, LotRecord.record_id == Record.id)
        .where(
            Record.id.in_(all_record_ids),
            LotRecord.lot_id == lot_id,
            Record.status == RecordStatus.qc_pending,
        )
    )
    eligible_ids = set(eligible_result.scalars().all())

    active_qc_result = await db.execute(
        select(Task.record_id).where(
            Task.record_id.in_(all_record_ids),
            Task.task_type == TaskType.qc,
            Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
        )
    )
    eligible_ids -= set(active_qc_result.scalars().all())

    ineligible_ids = [rid for rid in all_record_ids if rid not in eligible_ids]
    if ineligible_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "Records must belong to this lot, be qc_pending, and not already "
                f"assigned to a QC batch: ineligible record ids {ineligible_ids}"
            ),
        )

    batches: list[Batch] = []
    for assignment in assignments:
        agent_id = assignment["agent_id"]
        record_ids = assignment["record_ids"]

        batch = Batch(
            project_id=project_id,
            document_type_id=document_type_id,
            name="",
            batch_type=BatchType.qc,
            status=BatchStatus.indexing,
        )
        db.add(batch)
        await db.flush()
        # Name includes the DB-assigned id so it's guaranteed unique even when
        # several QC batches are created in the same request (one per agent
        # in the assignments loop, all within the same wall-clock second).
        batch.name = f"QC Batch {batch.id} — {datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

        for record_id in record_ids:
            task = Task(
                record_id=record_id,
                batch_id=batch.id,
                task_type=TaskType.qc,
                assigned_to=agent_id,
                assigned_by=supervisor_id,
                status=TaskStatus.pending,
            )
            db.add(task)

        batches.append(batch)

    await db.flush()
    return batches


async def _check_qc_batch_targets(
    db: AsyncSession,
    *,
    lot: Lot,
    project_id: int,
    document_type_id: int,
    agent_ids: list[int],
    tenant_id: int,
) -> None:
    """project_id, document_type_id and the agent ids all come from the
    request body. The QC batches must land in the lot's own project, under
    one of its document types, assigned to active QC agents of that
    project's customer organisation (the same set GET /projects/{id}/qc-agents
    offers) — not another customer's project or agents."""
    from app.models.document_type import DocumentType
    from app.models.project import Project
    from app.models.user import User, UserRole

    if project_id != lot.project_id:
        raise HTTPException(status_code=422, detail="project_id does not match the lot's project")
    doc_type = await db.get(DocumentType, document_type_id)
    if not doc_type or doc_type.project_id != lot.project_id:
        raise HTTPException(status_code=422, detail="document_type_id is not a document type of the lot's project")
    project = await db.get(Project, lot.project_id)
    valid = set((await db.execute(
        select(User.id).where(
            User.id.in_(set(agent_ids)),
            User.tenant_id == tenant_id,
            User.organization_id == project.customer_org_id,
            User.role == UserRole.customer_qc_agent,
            User.is_active == True,  # noqa: E712
        )
    )).scalars().all())
    invalid = sorted(set(agent_ids) - valid)
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Not active QC agents of this project's customer organisation: {invalid}",
        )


async def recall_lot(
    db: AsyncSession,
    *,
    lot_id: int,
    reason: str,
    user_id: int,
    tenant_id: int,
) -> Lot:
    """Digitizing supervisor takes a released lot back before the customer
    has sampled it. The lot returns to draft (hidden from the customer
    again) and its passing outgoing inspection is superseded, so it must be
    inspected afresh before it can be released again. Records keep their
    status; fixes go through the usual requeue/rework paths, which
    assert_releasable already waits for."""
    lot = await _get_lot_for_update(db, lot_id, tenant_id)
    if lot.status != LotStatus.released or lot.sample_size is not None:
        raise HTTPException(
            status_code=409,
            detail="Only a released lot the customer hasn't sampled yet can be recalled",
        )
    superseded = await lot_inspection_service.supersede_passed(db, lot)
    released_at, released_by = lot.released_at, lot.released_by
    lot.status = LotStatus.draft
    lot.released_at = None
    lot.released_by = None
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.lot,
        entity_id=lot.id,
        action=AuditAction.recalled,
        performed_by=user_id,
        old_value={
            "status": "released",
            "released_at": released_at.isoformat() if released_at else None,
            "released_by": released_by,
        },
        new_value={"status": "draft"},
        metadata={
            "reason": reason,
            "superseded_inspection_id": superseded.id if superseded else None,
        },
    )
    return lot


async def remove_lot_records(
    db: AsyncSession,
    *,
    lot_id: int,
    record_ids: list[int],
    user_id: int,
    tenant_id: int,
) -> Lot:
    """Take records out of a draft lot (e.g. after a recall, when the wrong
    records went in). The records themselves are untouched and can go into
    another lot. A passed inspection no longer describes the lot, so it is
    superseded; an in-progress one blocks the change, since its sample was
    drawn from the current set."""
    from app.models.lot_inspection import LotInspection, LotInspectionStatus

    lot = await _get_lot_for_update(db, lot_id, tenant_id)
    if lot.status != LotStatus.draft:
        raise HTTPException(status_code=409, detail="Records can only be removed from a draft lot")
    in_progress = (await db.execute(
        select(LotInspection.id).where(
            LotInspection.lot_id == lot_id, LotInspection.status == LotInspectionStatus.in_progress,
        )
    )).first()
    if in_progress:
        raise HTTPException(
            status_code=409, detail="Finish or cancel the inspection in progress before removing records",
        )

    wanted = set(record_ids)
    rows = list((await db.execute(
        select(LotRecord).where(LotRecord.lot_id == lot_id)
    )).scalars().all())
    to_remove = [lr for lr in rows if lr.record_id in wanted]
    missing = sorted(wanted - {lr.record_id for lr in to_remove})
    if missing:
        raise HTTPException(status_code=422, detail=f"Records not in this lot: {missing}")
    if len(to_remove) == len(rows):
        raise HTTPException(status_code=422, detail="A lot must keep at least one record")

    for lr in to_remove:
        await db.delete(lr)
    superseded = await lot_inspection_service.supersede_passed(db, lot)
    await db.flush()
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.lot,
        entity_id=lot.id,
        action=AuditAction.lot_records_removed,
        performed_by=user_id,
        old_value={"record_count": len(rows)},
        new_value={"record_count": len(rows) - len(to_remove)},
        metadata={
            "record_ids": sorted(wanted),
            "superseded_inspection_id": superseded.id if superseded else None,
        },
    )
    return lot


async def calculate_accuracy(
    db: AsyncSession,
    *,
    lot_id: int,
    tenant_id: int,
) -> Lot:
    """Called after a QC task completes or fails. Checks if all sampled records are done."""
    lot = await _get_lot(db, lot_id, tenant_id)

    sampled_result = await db.execute(
        select(LotRecord).where(LotRecord.lot_id == lot_id, LotRecord.is_sampled == True)  # noqa: E712
    )
    sampled = list(sampled_result.scalars().all())

    statuses = []
    for lr in sampled:
        record = await db.get(Record, lr.record_id)
        if record:
            statuses.append(record.status)

    done_statuses = {RecordStatus.qc_passed, RecordStatus.qc_failed}
    if not all(s in done_statuses for s in statuses):
        return lot  # Not all done yet

    passed = sum(1 for s in statuses if s == RecordStatus.qc_passed)
    total = len(statuses)
    accuracy = passed / total if total else 0.0
    lot.accuracy_rate = accuracy
    lot.qc_completed_at = datetime.now(timezone.utc)

    # acceptance_number is only ever set (by apply_sample) for lots sampled
    # under AQLConfig.sampling_mode="iso" — a reliable proxy for which rule
    # this lot should be evaluated under, without a second config lookup.
    any_critical_defect = None
    non_critical_defect_total = None
    if lot.acceptance_number is not None:
        from app.models.qc_field_result import QcFieldResult, QcFieldStatus

        record_ids = [lr.record_id for lr in sampled]
        defective_rows = (await db.execute(
            select(QcFieldResult.is_critical).where(
                QcFieldResult.lot_id == lot_id,
                QcFieldResult.record_id.in_(record_ids),
                QcFieldResult.status == QcFieldStatus.defective,
            )
        )).scalars().all()
        any_critical_defect = any(defective_rows)
        non_critical_defect_total = sum(1 for is_critical in defective_rows if not is_critical)
        lot.critical_defect_count = sum(1 for is_critical in defective_rows if is_critical)
        lot.minor_defect_count = non_critical_defect_total

        # A single critical-field defect fails the lot outright, independent
        # of the acceptance number — real ISO 2859-1 practice treats critical
        # defects as zero-tolerance, separate from the major/minor AQL table.
        if any_critical_defect:
            lot.status = LotStatus.failed
        else:
            lot.status = (
                LotStatus.passed if non_critical_defect_total <= lot.acceptance_number else LotStatus.failed
            )
    else:
        lot.status = LotStatus.passed if accuracy >= 0.9 else LotStatus.failed

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.lot,
        entity_id=lot.id,
        action=AuditAction.status_changed,
        performed_by=None,
        new_value={
            "status": lot.status,
            "accuracy_rate": accuracy,
            "passed": passed,
            "total": total,
            "acceptance_number": lot.acceptance_number,
            "any_critical_defect": any_critical_defect,
            "non_critical_defect_total": non_critical_defect_total,
        },
    )
    return lot


async def get_field_result_tabulation(
    db: AsyncSession,
    *,
    lot_id: int,
    tenant_id: int,
) -> dict:
    """Supervisor-facing aggregate of every QC agent's per-field marks on
    this lot — which fields were found defective, how often, by whom, and
    whether any critical-field defect makes the lot defective outright
    regardless of the non-critical tally against acceptance_number. Mirrors
    the same critical/non-critical split calculate_accuracy uses to decide
    the lot's actual pass/fail, so this view explains that decision."""
    from app.models.qc_field_result import QcFieldResult, QcFieldStatus
    from app.models.user import User

    lot = await _get_lot(db, lot_id, tenant_id)

    rows = (await db.execute(
        select(QcFieldResult, Task.assigned_to)
        .join(Task, Task.id == QcFieldResult.task_id)
        .where(QcFieldResult.lot_id == lot_id)
    )).all()

    agent_ids = {assigned_to for _, assigned_to in rows if assigned_to is not None}
    agents_by_id = {}
    if agent_ids:
        agents_result = await db.execute(select(User).where(User.id.in_(agent_ids)))
        agents_by_id = {u.id: u for u in agents_result.scalars().all()}

    fields: dict[str, dict] = {}
    for qfr, assigned_to in rows:
        # is_critical is snapshotted per-row (see QcFieldResult's docstring)
        # and could in theory differ across rows for the same field_key if
        # the schema's x-critical flag was edited mid-lot — first-seen value
        # wins here for a single per-field display flag; the pass/fail math
        # in calculate_accuracy always checks each row's own snapshot, not
        # this aggregate, so that decision is unaffected by this simplification.
        entry = fields.setdefault(qfr.field_key, {
            "field_key": qfr.field_key,
            "is_critical": qfr.is_critical,
            "defective_count": 0,
            "accepted_count": 0,
            "agent_ids": set(),
        })
        if qfr.status == QcFieldStatus.defective:
            entry["defective_count"] += 1
        else:
            entry["accepted_count"] += 1
        if assigned_to is not None:
            entry["agent_ids"].add(assigned_to)

    field_summaries = []
    any_critical_defect = False
    non_critical_defect_total = 0
    for entry in fields.values():
        if entry["is_critical"] and entry["defective_count"] > 0:
            any_critical_defect = True
        if not entry["is_critical"]:
            non_critical_defect_total += entry["defective_count"]
        field_summaries.append({
            "field_key": entry["field_key"],
            "is_critical": entry["is_critical"],
            "defective_count": entry["defective_count"],
            "accepted_count": entry["accepted_count"],
            "contributing_agents": [
                {"id": aid, "full_name": agents_by_id[aid].full_name}
                for aid in sorted(entry["agent_ids"]) if aid in agents_by_id
            ],
        })
    field_summaries.sort(key=lambda f: f["field_key"])

    return {
        "lot_id": lot_id,
        "any_critical_defect": any_critical_defect,
        "non_critical_defect_total": non_critical_defect_total,
        "acceptance_number": lot.acceptance_number,
        "fields": field_summaries,
    }


async def send_for_remediation(
    db: AsyncSession,
    *,
    lot_id: int,
    user_id: int,
    tenant_id: int,
) -> Lot:
    lot = await _get_lot(db, lot_id, tenant_id)
    if lot.status != LotStatus.failed:
        raise HTTPException(status_code=409, detail="Only failed lots can be sent for remediation")
    lot.status = LotStatus.remediation
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.lot,
        entity_id=lot.id,
        action=AuditAction.status_changed,
        performed_by=user_id,
        old_value={"status": "failed"},
        new_value={"status": "remediation"},
    )
    return lot
