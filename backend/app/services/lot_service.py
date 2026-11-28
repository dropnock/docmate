import math
import random
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import AQLStatus, GeneralLevel, SamplingMode
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.batch import Batch, BatchStatus, BatchType
from app.models.lot import Lot, LotRecord, LotStatus
from app.models.record import Record, RecordStatus
from app.models.task import Task, TaskStatus, TaskType
from app.services import audit_service, aql_service, defect_class_service, lot_inspection_service


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


async def count_lot_records(db: AsyncSession, *, lot_id: int) -> int:
    return (await db.execute(
        select(func.count()).select_from(LotRecord).where(LotRecord.lot_id == lot_id)
    )).scalar_one()


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

    # The level the supervisor set when inspecting the lot; lots inspected
    # before levels were set per lot fall back to the project's suggestion.
    level = lot.inspection_level or (config.current_status if config else AQLStatus.normal)
    general_level = config.general_level if config else GeneralLevel.II

    acceptance_number: int | None = None
    class_plan: dict | None = None
    if mode == SamplingMode.iso:
        if sample_rate is not None:
            raise HTTPException(
                status_code=422,
                detail="sample_rate must not be supplied when sampling_mode is 'iso' — the sample size is computed from the ISO 2859-1 table",
            )
        # One sample sized for the project's critical / major / minor AQLs;
        # each class is judged against its own Ac (calculate_accuracy).
        # acceptance_number keeps the major class's Ac.
        if total:
            plan = aql_service.class_plans(total, aql_service.config_class_aqls(config), level, general_level)
            class_plan = plan["classes"]
            acceptance_number = class_plan["major"]["acceptance_number"]
            sample_size = plan["sample_size"]
        else:
            sample_size = 0
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
    lot.class_plan = class_plan
    lot.aql_status_snapshot = level
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
            "inspection_level": level.value,
            "general_level": general_level.value,
            "sample_rate": derived_rate,
            "sample_size": sample_size,
            "acceptance_number": acceptance_number,
            "class_plan": class_plan,
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


# Who can be given customer QC work: the project's customer QC agents, and
# its customer supervisors (assigning batches to themselves or each other).
QC_ASSIGNEE_ROLES = ("customer_qc_agent", "customer_supervisor")


async def qc_assignees(db: AsyncSession, *, project_id: int, tenant_id: int) -> list:
    """Active users of the project's customer organisation who can be given
    QC batches, agents first, each group by name."""
    from app.models.project import Project
    from app.models.user import User, UserRole

    project = await db.get(Project, project_id)
    if project is None:
        return []
    users = (await db.execute(
        select(User).where(
            User.tenant_id == tenant_id,
            User.organization_id == project.customer_org_id,
            User.role.in_([UserRole(r) for r in QC_ASSIGNEE_ROLES]),
            User.is_active == True,  # noqa: E712
        )
    )).scalars().all()
    return sorted(users, key=lambda u: (u.role != UserRole.customer_qc_agent, u.full_name.lower()))


async def require_qc_assignees(db: AsyncSession, *, project_id: int, user_ids, tenant_id: int) -> None:
    """422 unless every id is an active QC agent or customer supervisor of
    the project's customer organisation."""
    valid = {u.id for u in await qc_assignees(db, project_id=project_id, tenant_id=tenant_id)}
    invalid = sorted(set(user_ids) - valid)
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Not active QC agents or supervisors of this project's customer organisation: {invalid}",
        )


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
    one of its document types, assigned to active QC agents or supervisors
    of that project's customer organisation (the same set GET
    /projects/{id}/qc-agents offers) — not another customer's project or
    people."""
    from app.models.document_type import DocumentType

    if project_id != lot.project_id:
        raise HTTPException(status_code=422, detail="project_id does not match the lot's project")
    doc_type = await db.get(DocumentType, document_type_id)
    if not doc_type or doc_type.project_id != lot.project_id:
        raise HTTPException(status_code=422, detail="document_type_id is not a document type of the lot's project")
    await require_qc_assignees(db, project_id=lot.project_id, user_ids=agent_ids, tenant_id=tenant_id)


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


async def resubmit_lot(
    db: AsyncSession,
    *,
    lot_id: int,
    user_id: int,
    tenant_id: int,
) -> Lot:
    """Digitizing supervisor resubmits a lot the customer failed and sent
    for remediation, once every record the customer rejected is back
    through QA. The finished round is archived as a LotQcRound, the lot's
    live sample/result fields are cleared, records the customer passed go
    back to qa_passed so they can be sampled again, and the lot returns to
    draft with its old outgoing inspection superseded: ISO 2859-1 treats a
    resubmitted lot as a new submission, so it is inspected again before
    release and the customer samples it afresh."""
    from app.models.lot import LotQcRound

    lot = await _get_lot_for_update(db, lot_id, tenant_id)
    if lot.status != LotStatus.remediation:
        raise HTTPException(status_code=409, detail="Only a lot in remediation can be resubmitted")

    lot_records = list((await db.execute(
        select(LotRecord).where(LotRecord.lot_id == lot_id)
    )).scalars().all())
    records = {r.id: r for r in (await db.execute(
        select(Record).where(Record.id.in_([lr.record_id for lr in lot_records]))
    )).scalars().all()}
    ready = {RecordStatus.qa_passed, RecordStatus.qc_passed}
    in_rework = sorted(rid for rid, r in records.items() if r.status not in ready)
    if in_rework:
        raise HTTPException(
            status_code=409,
            detail=f"{len(in_rework)} record(s) are still being reworked: {in_rework[:50]}",
        )

    archived = LotQcRound(
        tenant_id=tenant_id,
        lot_id=lot.id,
        round_number=lot.qc_round,
        status=LotStatus.failed,
        record_count=len(lot_records),
        sample_rate=lot.sample_rate,
        sample_size=lot.sample_size,
        acceptance_number=lot.acceptance_number,
        class_plan=lot.class_plan,
        accuracy_rate=lot.accuracy_rate,
        aql_status_snapshot=lot.aql_status_snapshot,
        critical_defect_count=lot.critical_defect_count,
        major_defect_count=lot.major_defect_count,
        minor_defect_count=lot.minor_defect_count,
        sampled_record_ids=sorted(lr.record_id for lr in lot_records if lr.is_sampled),
        released_at=lot.released_at,
        qc_completed_at=lot.qc_completed_at,
        decided_at=lot.decided_at,
        decided_by=lot.decided_by,
        resubmitted_by=user_id,
    )
    db.add(archived)

    reset = []
    for lr in lot_records:
        lr.is_sampled = False
        record = records.get(lr.record_id)
        if record is not None and record.status == RecordStatus.qc_passed:
            record.status = RecordStatus.qa_passed
            reset.append(record.id)
            await audit_service.write_event(
                db,
                tenant_id=tenant_id,
                entity_type=AuditEntityType.record,
                entity_id=record.id,
                action=AuditAction.status_changed,
                performed_by=user_id,
                old_value={"status": "qc_passed"},
                new_value={"status": "qa_passed"},
                metadata={"reason": "lot_resubmitted", "lot_id": lot.id, "qc_round": lot.qc_round},
            )

    superseded = await lot_inspection_service.supersede_passed(db, lot)
    old_round = lot.qc_round
    for field in (
        "sample_rate", "sample_size", "acceptance_number", "class_plan", "accuracy_rate", "aql_status_snapshot",
        "qc_completed_at", "critical_defect_count", "major_defect_count", "minor_defect_count",
        "released_at", "released_by", "decided_at", "decided_by",
    ):
        setattr(lot, field, None)
    lot.qc_round = old_round + 1
    lot.status = LotStatus.draft
    await db.flush()

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.lot,
        entity_id=lot.id,
        action=AuditAction.resubmitted,
        performed_by=user_id,
        old_value={"status": "remediation", "qc_round": old_round},
        new_value={"status": "draft", "qc_round": lot.qc_round},
        metadata={
            "archived_round_id": archived.id,
            "records_reset_to_qa_passed": reset,
            "superseded_inspection_id": superseded.id if superseded else None,
        },
    )
    return lot


async def list_qc_rounds(db: AsyncSession, *, lot_id: int) -> list:
    from app.models.lot import LotQcRound
    return list((await db.execute(
        select(LotQcRound).where(LotQcRound.lot_id == lot_id).order_by(LotQcRound.round_number)
    )).scalars().all())


async def _sampled(db: AsyncSession, lot_id: int) -> list[Record]:
    """The lot's sampled records (current round), in record id order."""
    return list((await db.execute(
        select(Record)
        .join(LotRecord, LotRecord.record_id == Record.id)
        .where(LotRecord.lot_id == lot_id, LotRecord.is_sampled == True)  # noqa: E712
        .order_by(Record.id)
    )).scalars().all())


async def _current_marks(db: AsyncSession, lot: Lot, record_ids: list[int]) -> list:
    """Every field mark of the current QC round on these records."""
    from app.models.qc_field_result import QcFieldResult
    if not record_ids:
        return []
    return list((await db.execute(
        select(QcFieldResult).where(
            QcFieldResult.lot_id == lot.id,
            QcFieldResult.qc_round == lot.qc_round,
            QcFieldResult.record_id.in_(record_ids),
        ).order_by(QcFieldResult.id)
    )).scalars().all())


async def _tally(db: AsyncSession, lot: Lot) -> dict:
    """Judge the current round's sample from its effective marks — the
    customer QC supervisor's overrides where there are any, otherwise the
    reviewers' marks (defect_class_service.effective). Writes nothing.
    Returns the figures the audit trail and the review screen show, with
    "accepted" the verdict the plan gives."""
    sampled = await _sampled(db, lot.id)
    passed = sum(1 for r in sampled if r.status == RecordStatus.qc_passed)
    total = len(sampled)
    accuracy = passed / total if total else 0.0
    out = {
        "passed": passed, "total": total, "accuracy_rate": accuracy,
        "acceptance_number": lot.acceptance_number,
        "any_critical_defect": None, "non_critical_defect_total": None, "defects_by_class": None,
    }

    # acceptance_number is only ever set (by apply_sample) for lots sampled
    # under AQLConfig.sampling_mode="iso" — a reliable proxy for which rule
    # this lot should be evaluated under, without a second config lookup.
    if lot.acceptance_number is None:
        out["accepted"] = accuracy >= 0.9
        return out

    severities = [
        sev for status, _, sev in (
            defect_class_service.effective(m) for m in await _current_marks(db, lot, [r.id for r in sampled])
        ) if status == "defective"
    ]
    counts = defect_class_service.tally(severities)
    non_critical = counts["major"] + counts["minor"]
    out["any_critical_defect"] = counts["critical"] > 0
    out["non_critical_defect_total"] = non_critical
    out["defects_by_class"] = counts
    if lot.class_plan:
        # Non-conformities added up per class across the sample, each
        # class against its own Ac (defect_class_service).
        out["accepted"] = defect_class_service.judge(counts, lot.class_plan)["accepted"]
    else:
        # Lots sampled before defect classes: a single critical-field defect
        # fails the lot outright, independent of the acceptance number; the
        # other defects together against it.
        out["accepted"] = counts["critical"] == 0 and non_critical <= lot.acceptance_number
    return out


async def _judge(db: AsyncSession, lot: Lot) -> dict:
    """_tally, with the counts and accuracy written onto the lot."""
    out = await _tally(db, lot)
    lot.accuracy_rate = out["accuracy_rate"]
    counts = out["defects_by_class"]
    if counts is not None:
        lot.critical_defect_count = counts["critical"]
        if lot.class_plan:
            lot.major_defect_count = counts["major"]
            lot.minor_defect_count = counts["minor"]
        else:
            lot.minor_defect_count = out["non_critical_defect_total"]
    if not lot.class_plan:
        out["defects_by_class"] = None
    return out


async def calculate_accuracy(
    db: AsyncSession,
    *,
    lot_id: int,
    tenant_id: int,
) -> Lot:
    """Called after a QC task completes or fails. Once every sampled record
    has been reviewed, the lot waits for the customer QC supervisor: they
    review the defects (override_qc_mark) and confirm the verdict
    (decide_lot). The provisional counts are stored now so the lot list
    shows them."""
    lot = await _get_lot(db, lot_id, tenant_id)
    if lot.status != LotStatus.qc_in_progress:
        return lot

    sampled = await _sampled(db, lot_id)
    done_statuses = {RecordStatus.qc_passed, RecordStatus.qc_failed}
    if not sampled or not all(r.status in done_statuses for r in sampled):
        return lot  # Not all done yet
    # Reviewers can still go back to records in a QC batch they haven't
    # submitted (submit_qc_batch), so the lot waits for those too.
    if await _open_qc_batch_ids(db, [r.id for r in sampled]):
        return lot

    result = await _judge(db, lot)
    lot.status = LotStatus.awaiting_decision
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.lot,
        entity_id=lot.id,
        action=AuditAction.status_changed,
        performed_by=None,
        old_value={"status": "qc_in_progress"},
        new_value={"status": "awaiting_decision", **result},
    )
    return lot


async def _open_qc_batch_ids(db: AsyncSession, record_ids: list[int]) -> set[int]:
    """The QC batches holding these records' QC tasks that haven't been
    submitted yet."""
    if not record_ids:
        return set()
    return set((await db.execute(
        select(Batch.id).join(Task, Task.batch_id == Batch.id).where(
            Task.record_id.in_(record_ids),
            Task.task_type == TaskType.qc,
            Batch.batch_type == BatchType.qc,
            Batch.status == BatchStatus.indexing,
        )
    )).scalars().all())


async def submit_qc_batch(db: AsyncSession, *, batch: Batch, user, tenant_id: int) -> Batch:
    """The customer QC reviewer's "Submit batch": until now they can go back
    to any record of the batch and review it again (task_service.start_task
    reopens it). Every record must be reviewed first. Submitting closes the
    batch and, once every QC batch of a lot is in, the lot moves on to the
    customer QC supervisor (calculate_accuracy)."""
    if batch.batch_type != BatchType.qc:
        raise HTTPException(status_code=400, detail="Not a QC batch")
    if batch.status != BatchStatus.indexing:
        raise HTTPException(status_code=409, detail="This QC batch has already been submitted")
    tasks = list((await db.execute(
        select(Task).where(Task.batch_id == batch.id, Task.task_type == TaskType.qc)
    )).scalars().all())
    role = getattr(user.role, "value", user.role)
    if role not in ("customer_supervisor", "admin") and not any(t.assigned_to == user.id for t in tasks):
        raise HTTPException(status_code=403, detail="This QC batch isn't assigned to you")
    open_tasks = [t for t in tasks if t.status in (TaskStatus.pending, TaskStatus.in_progress)]
    if open_tasks:
        n = len(open_tasks)
        raise HTTPException(
            status_code=400,
            detail=f"{n} record{'' if n == 1 else 's'} still need{'s' if n == 1 else ''} a review "
                   f"before the batch can be submitted",
        )

    batch.status = BatchStatus.complete
    passed = sum(1 for t in tasks if t.status == TaskStatus.completed)
    await audit_service.write_event(
        db, tenant_id=tenant_id, entity_type=AuditEntityType.batch, entity_id=batch.id,
        action=AuditAction.status_changed, performed_by=user.id,
        old_value={"status": "indexing"}, new_value={"status": "complete"},
        metadata={"qc_submitted": True, "passed": passed, "rejected": len(tasks) - passed},
    )
    await db.flush()

    lot_ids = set((await db.execute(
        select(LotRecord.lot_id).where(
            LotRecord.record_id.in_([t.record_id for t in tasks]),
            LotRecord.is_sampled == True,  # noqa: E712
        )
    )).scalars().all()) if tasks else set()
    for lot_id in sorted(lot_ids):
        await calculate_accuracy(db, lot_id=lot_id, tenant_id=tenant_id)
    await db.flush()
    return batch


async def _review_lot(db: AsyncSession, lot_id: int, tenant_id: int) -> Lot:
    lot = await _get_lot_for_update(db, lot_id, tenant_id)
    if lot.status != LotStatus.awaiting_decision:
        raise HTTPException(
            status_code=409,
            detail="The lot's QC results can only be reviewed while it is awaiting your decision",
        )
    return lot


async def _sampled_record(db: AsyncSession, lot: Lot, record_id: int) -> Record:
    lr = (await db.execute(
        select(LotRecord).where(
            LotRecord.lot_id == lot.id, LotRecord.record_id == record_id,
            LotRecord.is_sampled == True,  # noqa: E712
        )
    )).scalar_one_or_none()
    record = await db.get(Record, record_id) if lr else None
    if record is None:
        raise HTTPException(status_code=404, detail="Record is not in this lot's sample")
    return record


async def _set_record_outcome(
    db: AsyncSession, *, record: Record, failed: bool, lot: Lot, user_id: int, tenant_id: int, note: str | None,
) -> None:
    new = RecordStatus.qc_failed if failed else RecordStatus.qc_passed
    if record.status == new:
        return
    old = record.status
    record.status = new
    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.record,
        entity_id=record.id,
        action=AuditAction.status_changed,
        performed_by=user_id,
        old_value={"status": old.value},
        new_value={"status": new.value},
        metadata={"reason": "qc_supervisor_review", "lot_id": lot.id, "qc_round": lot.qc_round, "note": note},
    )


def _mark_out(status, error_type, severity, note=None) -> dict:
    return {
        "status": getattr(status, "value", status),
        "error_type": getattr(error_type, "value", error_type),
        "severity": getattr(severity, "value", severity),
        "note": note,
    }


async def override_qc_mark(
    db: AsyncSession,
    *,
    lot_id: int,
    record_id: int,
    field_key: str,
    status: str,
    error_type: str | None,
    note: str | None,
    user_id: int,
    tenant_id: int,
) -> dict:
    """The customer QC supervisor's ruling on one field of a sampled record:
    uphold the reviewer's mark, dismiss a defect, give it another error type
    (and so, through the project's agreement, another class) or mark an
    accepted field defective. The reviewer's mark is kept; the ruling is
    stored beside it and is what the counts and the verdict use. A ruling
    that matches the reviewer's mark clears any override. The record's
    status follows its effective marks; rework waits for decide_lot."""
    from app.models.defect import ErrorType
    from app.models.qc_field_result import QcFieldStatus

    lot = await _review_lot(db, lot_id, tenant_id)
    if lot.acceptance_number is None:
        raise HTTPException(status_code=409, detail="This lot was sampled at a manual rate; review whole records instead")
    record = await _sampled_record(db, lot, record_id)
    marks = await _current_marks(db, lot, [record.id])
    mark = next((m for m in marks if m.field_key == field_key), None)
    if mark is None:
        raise HTTPException(status_code=404, detail=f"No QC mark for field {field_key!r} on this record")

    try:
        new_status = QcFieldStatus(status)
    except ValueError:
        raise HTTPException(status_code=422, detail="status must be accepted or defective")
    new_error_type = None
    new_severity = None
    if new_status == QcFieldStatus.defective:
        if error_type is None and lot.class_plan:
            raise HTTPException(
                status_code=422, detail="Say what is wrong with the field (missing, typo or interpretation)",
            )
        if error_type is not None:
            try:
                new_error_type = ErrorType(error_type)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"Unknown error type {error_type!r}")
        error_classes = await defect_class_service.project_error_classes(db, lot.project_id)
        new_severity = defect_class_service.classify(
            is_identifier=mark.is_critical, error_type=new_error_type, error_classes=error_classes,
        )

    before = _mark_out(*defect_class_service.effective(mark), mark.override_note if mark.override_status else None)
    reviewer = defect_class_service.effective(mark, overrides=False)
    same_as_reviewer = (
        reviewer[0] == new_status.value
        and (new_status == QcFieldStatus.accepted or (reviewer[1] == new_error_type and reviewer[2] == new_severity))
    )
    note = (note or "").strip() or None
    if not same_as_reviewer and not note:
        raise HTTPException(status_code=422, detail="Give a reason for changing the reviewer's mark")

    now = datetime.now(timezone.utc)
    if same_as_reviewer:
        mark.override_status = mark.override_error_type = mark.override_severity = None
        mark.override_note = None
        mark.overridden_by = mark.overridden_at = None
    else:
        mark.override_status = new_status
        mark.override_error_type = new_error_type
        mark.override_severity = new_severity
        mark.override_note = note
        mark.overridden_by = user_id
        mark.overridden_at = now
    after = _mark_out(new_status, new_error_type, new_severity, None if same_as_reviewer else note)
    if before != after:
        await audit_service.write_event(
            db,
            tenant_id=tenant_id,
            entity_type=AuditEntityType.record,
            entity_id=record.id,
            action=AuditAction.qc_mark_overridden,
            performed_by=user_id,
            old_value=before,
            new_value=after,
            metadata={
                "lot_id": lot.id, "qc_round": lot.qc_round, "field_key": field_key,
                "reviewer_mark": _mark_out(*reviewer, mark.note), "reverted_to_reviewer": same_as_reviewer,
            },
        )

    failed = any(defect_class_service.effective(m)[0] == "defective" for m in marks)
    await _set_record_outcome(
        db, record=record, failed=failed, lot=lot, user_id=user_id, tenant_id=tenant_id, note=note,
    )
    await db.flush()
    return await _judge(db, lot)


async def override_record_outcome(
    db: AsyncSession,
    *,
    lot_id: int,
    record_id: int,
    passed: bool,
    note: str | None,
    user_id: int,
    tenant_id: int,
) -> dict:
    """Manual-rate lots have no field marks: the supervisor rules on the
    whole record — pass one a reviewer rejected, or reject one they passed.
    A reason is needed whenever the ruling goes against the reviewer."""
    lot = await _review_lot(db, lot_id, tenant_id)
    if lot.acceptance_number is not None:
        raise HTTPException(status_code=409, detail="This lot was sampled under ISO 2859-1; review its fields instead")
    record = await _sampled_record(db, lot, record_id)
    task = (await latest_qc_tasks(db, [record.id])).get(record.id)
    reviewer_passed = task is not None and task.status == TaskStatus.completed
    note = (note or "").strip() or None
    if passed != reviewer_passed and not note:
        raise HTTPException(status_code=422, detail="Give a reason for changing the reviewer's result")
    if (record.status == RecordStatus.qc_passed) != passed:
        await audit_service.write_event(
            db,
            tenant_id=tenant_id,
            entity_type=AuditEntityType.record,
            entity_id=record.id,
            action=AuditAction.qc_mark_overridden,
            performed_by=user_id,
            old_value={"passed": record.status == RecordStatus.qc_passed},
            new_value={"passed": passed, "note": note},
            metadata={
                "lot_id": lot.id, "qc_round": lot.qc_round,
                "reviewer_passed": reviewer_passed, "reverted_to_reviewer": passed == reviewer_passed,
            },
        )
    await _set_record_outcome(
        db, record=record, failed=not passed, lot=lot, user_id=user_id, tenant_id=tenant_id, note=note,
    )
    await db.flush()
    return await _judge(db, lot)


async def decide_lot(
    db: AsyncSession,
    *,
    lot_id: int,
    user_id: int,
    tenant_id: int,
) -> Lot:
    """The customer QC supervisor confirms the lot's verdict once they have
    reviewed its defects. The verdict is the sampling plan's, judged on the
    effective marks; records still rejected go back to the digitizing
    entity for rework now, not when the reviewer rejected them."""
    from app.models.qc_field_result import QcFieldResult
    from app.services.task_service import route_to_rework

    lot = await _review_lot(db, lot_id, tenant_id)
    result = await _judge(db, lot)
    now = datetime.now(timezone.utc)
    lot.status = LotStatus.passed if result["accepted"] else LotStatus.failed
    lot.qc_completed_at = now
    lot.decided_at = now
    lot.decided_by = user_id

    # ISO 2859-1 switching counts each lot once, on its original submission:
    # a resubmitted lot's later rounds don't move the project's status.
    if lot.qc_round == 1:
        await aql_service.record_lot_outcome(
            db, lot=lot, passed=lot.status == LotStatus.passed, tenant_id=tenant_id,
        )

    sampled = await _sampled(db, lot.id)
    rejected = [r for r in sampled if r.status == RecordStatus.qc_failed]
    tasks = await latest_qc_tasks(db, [r.id for r in rejected])
    for record in rejected:
        await route_to_rework(db, record=record, qc_task=tasks.get(record.id), user_id=user_id)

    overrides = (await db.execute(
        select(func.count()).select_from(QcFieldResult).where(
            QcFieldResult.lot_id == lot.id, QcFieldResult.qc_round == lot.qc_round,
            QcFieldResult.override_status.is_not(None),
        )
    )).scalar_one()
    record_overrides = (await db.execute(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == AuditAction.qc_mark_overridden,
            AuditLog.entity_type == AuditEntityType.record,
            AuditLog.entity_id.in_([r.id for r in sampled] or [-1]),
        )
    )).scalar_one() if lot.acceptance_number is None else None

    await audit_service.write_event(
        db,
        tenant_id=tenant_id,
        entity_type=AuditEntityType.lot,
        entity_id=lot.id,
        action=AuditAction.qc_lot_decided,
        performed_by=user_id,
        old_value={"status": "awaiting_decision"},
        new_value={"status": lot.status.value, **result},
        metadata={
            "qc_round": lot.qc_round,
            "fields_overridden": overrides,
            "record_rulings": record_overrides,
            "records_sent_for_rework": [r.id for r in rejected],
        },
    )
    return lot


async def get_qc_review(
    db: AsyncSession,
    *,
    lot_id: int,
    tenant_id: int,
) -> dict:
    """Everything the customer QC supervisor's review screen needs: every
    sampled record of the current round with the reviewer, their outcome,
    and per field the reviewer's mark, the supervisor's override and the
    mark that counts; plus the provisional verdict."""
    from app.models.user import User

    lot = await _get_lot(db, lot_id, tenant_id)
    sampled = await _sampled(db, lot.id)
    marks = await _current_marks(db, lot, [r.id for r in sampled])
    tasks = await latest_qc_tasks(db, [r.id for r in sampled])
    user_ids = {t.assigned_to for t in tasks.values() if t.assigned_to} | {
        m.overridden_by for m in marks if m.overridden_by
    }
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(user_ids or {-1})))).scalars()}

    batch_ids = {t.batch_id for t in tasks.values()}
    doc_types = dict((await db.execute(
        select(Batch.id, Batch.document_type_id).where(Batch.id.in_(batch_ids or {-1}))
    )).all())

    by_record: dict[int, list] = {}
    for m in marks:
        by_record.setdefault(m.record_id, []).append(m)

    records = []
    for r in sampled:
        task = tasks.get(r.id)
        fields = []
        for m in by_record.get(r.id, []):
            eff = defect_class_service.effective(m)
            reviewer = defect_class_service.effective(m, overrides=False)
            fields.append({
                "field_key": m.field_key,
                "is_critical": m.is_critical,
                "reviewer": _mark_out(*reviewer, m.note),
                "override": _mark_out(m.override_status, m.override_error_type, m.override_severity, m.override_note)
                if m.override_status is not None else None,
                "overridden_by": users[m.overridden_by].full_name if m.overridden_by in users else None,
                "overridden_at": m.overridden_at,
                "effective": _mark_out(*eff),
            })
        reviewer_user = users.get(task.assigned_to) if task else None
        records.append({
            "record_id": r.id,
            "source_identifier": r.source_identifier,
            "original_filename": r.original_filename,
            "document_type_id": doc_types.get(task.batch_id) if task else None,
            "indexed_data": r.indexed_data or {},
            "status": r.status.value,
            "qc_task_id": task.id if task else None,
            "reviewer_passed": task.status == TaskStatus.completed if task else None,
            "reviewer_name": reviewer_user.full_name if reviewer_user else None,
            "reviewed_at": task.completed_at if task else None,
            "defect_count": sum(1 for f in fields if f["effective"]["status"] == "defective"),
            "overridden": any(f["override"] for f in fields),
            "fields": fields,
        })

    result = await _tally(db, lot) if sampled else None
    classes = None
    if lot.class_plan and result is not None:
        classes = defect_class_service.judge(result["defects_by_class"], lot.class_plan)["classes"]
    return {
        "lot_id": lot.id,
        "lot_name": lot.name,
        "project_id": lot.project_id,
        "status": lot.status.value,
        "qc_round": lot.qc_round,
        "sample_size": lot.sample_size,
        "error_classes": await defect_class_service.project_error_classes(db, lot.project_id),
        "iso": lot.acceptance_number is not None,
        "acceptance_number": lot.acceptance_number,
        "classes": classes,
        "accepted": result["accepted"] if result else None,
        "accuracy_rate": result["accuracy_rate"] if result else None,
        "any_critical_defect": result["any_critical_defect"] if result else None,
        "non_critical_defect_total": result["non_critical_defect_total"] if result else None,
        "records": records,
    }


async def get_field_result_tabulation(
    db: AsyncSession,
    *,
    lot_id: int,
    tenant_id: int,
) -> dict:
    """Supervisor-facing aggregate of every QC agent's per-field marks on
    this lot (the supervisor's overrides where they made one) — which
    fields were found defective, how often, by whom, and
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
        .where(QcFieldResult.lot_id == lot_id, QcFieldResult.qc_round == lot.qc_round)
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
            "by_class": {"critical": 0, "major": 0, "minor": 0},
            "by_error_type": {},
        })
        # The mark that counts: the customer QC supervisor's override, if any.
        status, error_type, severity = defect_class_service.effective(qfr)
        if status == QcFieldStatus.defective.value:
            entry["defective_count"] += 1
            entry["by_class"][severity.value] += 1
            if error_type:
                et = getattr(error_type, "value", error_type)
                entry["by_error_type"][et] = entry["by_error_type"].get(et, 0) + 1
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
            "by_class": entry["by_class"],
            "by_error_type": entry["by_error_type"],
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
        # Per-class plan with the current round's totals (None for lots
        # sampled before defect classes): found / accepted per class.
        "classes": defect_class_service.judge(
            defect_class_service.tally(
                s for f in field_summaries for s, n in f["by_class"].items() for _ in range(n)
            ),
            lot.class_plan,
        )["classes"] if lot.class_plan else None,
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


async def latest_qc_tasks(db: AsyncSession, record_ids: list[int]) -> dict[int, Task]:
    """Each record's most recent QC task (the current round's), by record id."""
    if not record_ids:
        return {}
    tasks = (await db.execute(
        select(Task).where(Task.record_id.in_(record_ids), Task.task_type == TaskType.qc).order_by(Task.id)
    )).scalars().all()
    return {t.record_id: t for t in tasks}


async def list_qc_batches(db: AsyncSession, *, project_id: int, lot_id: int | None = None) -> list[dict]:
    """Every customer QC batch of the project (or of one lot), newest first,
    with who holds its tasks and how far it has got."""
    from app.models.user import User

    batches = list((await db.execute(
        select(Batch).where(Batch.project_id == project_id, Batch.batch_type == BatchType.qc)
        .order_by(Batch.id.desc())
    )).scalars().all())
    if not batches:
        return []
    tasks = list((await db.execute(
        select(Task).where(Task.batch_id.in_([b.id for b in batches]), Task.task_type == TaskType.qc)
    )).scalars().all())
    lot_of_record = dict((await db.execute(
        select(LotRecord.record_id, LotRecord.lot_id).where(LotRecord.record_id.in_({t.record_id for t in tasks}))
    )).all()) if tasks else {}
    lots = {lot.id: lot for lot in (await db.execute(
        select(Lot).where(Lot.id.in_(set(lot_of_record.values())))
    )).scalars()} if lot_of_record else {}
    users = {u.id: u for u in (await db.execute(
        select(User).where(User.id.in_({t.assigned_to for t in tasks if t.assigned_to}))
    )).scalars()} if tasks else {}

    by_batch: dict[int, list[Task]] = {}
    for t in tasks:
        by_batch.setdefault(t.batch_id, []).append(t)

    out = []
    for b in batches:
        btasks = by_batch.get(b.id, [])
        lot_ids = {lot_of_record.get(t.record_id) for t in btasks} - {None}
        batch_lot = lots.get(min(lot_ids)) if lot_ids else None
        if lot_id is not None and (batch_lot is None or batch_lot.id != lot_id):
            continue
        counts = {s.value: 0 for s in TaskStatus}
        holders: dict[int, dict] = {}
        for t in btasks:
            counts[t.status.value] += 1
            if t.assigned_to is None:
                continue
            u = users.get(t.assigned_to)
            h = holders.setdefault(t.assigned_to, {
                "id": t.assigned_to, "full_name": u.full_name if u else str(t.assigned_to),
                "role": u.role.value if u else None, "tasks": 0, "open": 0,
            })
            h["tasks"] += 1
            if t.status in (TaskStatus.pending, TaskStatus.in_progress):
                h["open"] += 1
        out.append({
            "id": b.id, "name": b.name, "document_type_id": b.document_type_id, "created_at": b.created_at,
            "submitted": b.status != BatchStatus.indexing,
            "lot_id": batch_lot.id if batch_lot else None, "lot_name": batch_lot.name if batch_lot else None,
            "total": len(btasks), "pending": counts["pending"], "in_progress": counts["in_progress"],
            "completed": counts["completed"], "failed": counts["failed"],
            "assignees": sorted(holders.values(), key=lambda h: (-h["open"], h["full_name"].lower())),
        })
    return out
