"""
ISO 2859-1 Acceptance Sampling — general inspection levels I / II / III
(per project, default II). Sample size code letters and
acceptance/rejection numbers.
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import AQLConfig, AQLStatus, GeneralLevel, SamplingMode
from app.models.audit_log import AuditAction, AuditEntityType
from app.services import audit_service

# ISO 2859-1 Table 1: lot size -> sample size code letter, per general
# inspection level. Each entry is the upper bound of a lot-size range;
# lots above 500 000 take the last letter (I: N, II: Q, III: R).
# Level II is the standard's default; III samples more (more
# discrimination), I fewer.
_LOT_SIZE_BOUNDS = (8, 15, 25, 50, 90, 150, 280, 500, 1200, 3200, 10000, 35000, 150000, 500000)
_CODE_LETTERS_BY_GENERAL_LEVEL: dict[GeneralLevel, str] = {
    #                  2   9  16  26  51  91 151 281 501 1201 3201 10001 35001 150001 500001+
    GeneralLevel.I:   "AABCCDEFGHJKLMN",
    GeneralLevel.II:  "ABCDEFGHJKLMNPQ",
    GeneralLevel.III: "BCDEFGHJKLMNPQR",
}

_LETTERS = "ABCDEFGHJKLMNPQRS"

# Single sampling master tables (ISO 2859-1 Tables 2-A, 2-B, 2-C) for the
# AQL columns DocMate offers. Each cell is the acceptance number Ac (the
# rejection number is always Ac + 1), or an arrow: "v" = use the first plan
# below, "^" = use the first plan above — the sample size then comes from
# the row the arrow lands on, not the lot's own code letter. Reduced
# inspection uses the 1999 edition's plans (Re = Ac + 1, no gap).
AVAILABLE_AQLS: tuple[float, ...] = (0.65, 1.0, 1.5, 2.5)

_SAMPLE_SIZES: dict[AQLStatus, dict[str, int]] = {
    AQLStatus.normal: dict(zip(_LETTERS, (2, 3, 5, 8, 13, 20, 32, 50, 80, 125, 200, 315, 500, 800, 1250, 2000))),
    AQLStatus.tightened: dict(zip(_LETTERS, (2, 3, 5, 8, 13, 20, 32, 50, 80, 125, 200, 315, 500, 800, 1250, 2000, 3150))),
    AQLStatus.reduced: dict(zip(_LETTERS, (2, 2, 2, 3, 5, 8, 13, 20, 32, 50, 80, 125, 200, 315, 500, 800))),
}

#                 AQL:  0.65   1.0   1.5   2.5
_NORMAL_AC: dict[str, tuple] = {
    "A": ("v", "v", "v", "v"),
    "B": ("v", "v", "v", "v"),
    "C": ("v", "v", "v", 0),
    "D": ("v", "v", 0, "^"),
    "E": ("v", 0, "^", "v"),
    "F": (0, "^", "v", 1),
    "G": ("^", "v", 1, 2),
    "H": ("v", 1, 2, 3),
    "J": (1, 2, 3, 5),
    "K": (2, 3, 5, 7),
    "L": (3, 5, 7, 10),
    "M": (5, 7, 10, 14),
    "N": (7, 10, 14, 21),
    "P": (10, 14, 21, "^"),
    "Q": (14, 21, "^", "^"),
    "R": (21, "^", "^", "^"),
}

#                 AQL:  0.65   1.0   1.5   2.5
_TIGHTENED_AC: dict[str, tuple] = {
    "A": ("v", "v", "v", "v"),
    "B": ("v", "v", "v", "v"),
    "C": ("v", "v", "v", "v"),
    "D": ("v", "v", "v", "v"),
    "E": ("v", "v", "v", 0),
    "F": ("v", "v", 0, "v"),
    "G": ("v", 0, "v", 1),
    "H": (0, "v", 1, 2),
    "J": ("v", 1, 2, 3),
    "K": (1, 2, 3, 5),
    "L": (2, 3, 5, 8),
    "M": (3, 5, 8, 12),
    "N": (5, 8, 12, 18),
    "P": (8, 12, 18, "^"),
    "Q": (12, 18, "^", "^"),
    "R": (18, "^", "^", "^"),
    "S": ("^", "^", "^", "^"),
}

# Reduced: each code letter takes the normal-inspection cell of the letter
# whose (normal) sample size equals its own reduced sample size — e.g. K's
# reduced n=50 is H's normal n, so K reduced = H normal. A, B and C all
# reduce to n=2 (A).
_REDUCED_AC: dict[str, tuple] = {
    letter: _NORMAL_AC[_LETTERS[max(0, i - 2)]] for i, letter in enumerate(_LETTERS[:16])
}

_AC_TABLES: dict[AQLStatus, dict[str, tuple]] = {
    AQLStatus.normal: _NORMAL_AC,
    AQLStatus.tightened: _TIGHTENED_AC,
    AQLStatus.reduced: _REDUCED_AC,
}

INSPECTION_LEVELS: tuple[AQLStatus, ...] = (AQLStatus.normal, AQLStatus.tightened, AQLStatus.reduced)


def _code_letter(batch_size: int, general_level: GeneralLevel = GeneralLevel.II) -> str:
    letters = _CODE_LETTERS_BY_GENERAL_LEVEL[GeneralLevel(general_level)]
    for i, max_size in enumerate(_LOT_SIZE_BOUNDS):
        if batch_size <= max_size:
            return letters[i]
    return letters[-1]


def _closest_aql(aql: float) -> float:
    """Return the nearest AQL column available in the sampling tables."""
    return min(AVAILABLE_AQLS, key=lambda k: abs(k - aql))


def _resolve(level: AQLStatus, letter: str, aql: float) -> tuple[str, int, int]:
    """Follow the table's arrows from `letter` to the plan that applies:
    returns (plan_letter, table_sample_size, acceptance_number)."""
    table = _AC_TABLES[level]
    col = AVAILABLE_AQLS.index(aql)
    i = _LETTERS.index(letter)
    while True:
        cell = table[_LETTERS[i]][col]
        if cell == "v":
            i += 1
        elif cell == "^":
            i -= 1
        else:
            plan_letter = _LETTERS[i]
            return plan_letter, _SAMPLE_SIZES[level][plan_letter], cell


def compute_sample_size(
    batch_size: int, aql_level: float, inspection_level: AQLStatus = AQLStatus.normal,
    general_level: GeneralLevel = GeneralLevel.II,
) -> tuple[int, int]:
    """Return (table_sample_size, acceptance_number) for a lot size and AQL,
    snapping an untabulated AQL to the nearest column. Callers cap the
    sample at the lot size."""
    _, n, ac = _resolve(
        AQLStatus(inspection_level), _code_letter(batch_size, general_level), _closest_aql(aql_level),
    )
    return n, ac


def sampling_plan(
    lot_size: int, aql_level: float, inspection_level: AQLStatus = AQLStatus.normal,
    general_level: GeneralLevel = GeneralLevel.II,
) -> dict[str, Any]:
    """Single sampling plan, at the given general inspection level, for an exact
    tabulated AQL and an inspection level (normal / tightened / reduced) —
    unlike compute_sample_size, which snaps to the nearest column, an AQL
    that isn't in the table is rejected, so a report can never claim an AQL
    the plan wasn't actually drawn from. code_letter is the lot's own
    letter; when the table points elsewhere (an arrow), the sample size and
    Ac/Re come from that row. The sample is capped at the lot size (100%
    inspection when the table's sample size meets or exceeds the lot)."""
    if aql_level not in AVAILABLE_AQLS:
        raise HTTPException(
            status_code=422,
            detail=f"aql_level must be one of {list(AVAILABLE_AQLS)}",
        )
    letter = _code_letter(lot_size, general_level)
    _, table_size, acceptance_number = _resolve(AQLStatus(inspection_level), letter, aql_level)
    return {
        "code_letter": letter,
        "sample_size": min(table_size, lot_size),
        "acceptance_number": acceptance_number,
        "rejection_number": acceptance_number + 1,
    }


def plans_by_level(
    lot_size: int, aql_level: float, general_level: GeneralLevel = GeneralLevel.II,
) -> dict[str, dict[str, Any]]:
    """The plan each inspection level would give this lot — what the
    supervisor chooses between."""
    aql = _closest_aql(aql_level)
    return {lvl.value: sampling_plan(lot_size, aql, lvl, general_level) for lvl in INSPECTION_LEVELS}


async def get_config(db: AsyncSession, project_id: int) -> AQLConfig | None:
    """AQLConfig's primary key is `id`; `project_id` is a separate unique
    column — db.get(AQLConfig, project_id) would look up by PK, not this
    column, and only return the right row by coincidence. Every lookup by
    project should go through this."""
    result = await db.execute(select(AQLConfig).where(AQLConfig.project_id == project_id))
    return result.scalar_one_or_none()


async def get_current_aql_level(db: AsyncSession, project_id: int) -> float:
    """The project's AQL, as tabulated. The inspection level (normal /
    tightened / reduced) no longer changes the AQL — it picks the table the
    plan comes from — so this is always the project's configured AQL
    (AQLConfig.normal_aql), snapped to the nearest tabulated column."""
    config = await get_config(db, project_id)
    if config is None:
        return 1.5
    return _closest_aql(config.normal_aql)


async def get_general_level(db: AsyncSession, project_id: int) -> GeneralLevel:
    """The project's ISO 2859-1 general inspection level (I / II / III),
    which picks the code-letter column of Table 1. II when unconfigured."""
    config = await get_config(db, project_id)
    return config.general_level if config else GeneralLevel.II


async def get_suggested_level(db: AsyncSession, project_id: int) -> AQLStatus:
    """The inspection level ISO 2859-1's switching rules suggest for the
    project's next lot (AQLConfig.current_status, moved by apply_switching
    on each lot's first-round customer QC verdict). Only a suggestion: the
    digitizing supervisor sets each lot's level when inspecting it."""
    config = await get_config(db, project_id)
    return config.current_status if config else AQLStatus.normal


async def update_config(
    db: AsyncSession,
    *,
    project_id: int,
    updates: dict[str, Any],
    user_id: int,
    tenant_id: int,
) -> AQLConfig:
    """Apply a partial update to a project's AQLConfig, writing one audit
    event with the diff of only the fields that actually changed."""
    config = await get_config(db, project_id)
    if config is None:
        raise HTTPException(status_code=404, detail="No AQL config for project")

    if updates.get("normal_aql") is not None and updates["normal_aql"] not in AVAILABLE_AQLS:
        raise HTTPException(
            status_code=422, detail=f"normal_aql must be one of {list(AVAILABLE_AQLS)}",
        )

    old_value: dict[str, Any] = {}
    new_value: dict[str, Any] = {}
    for field, value in updates.items():
        if field == "sampling_mode":
            value = SamplingMode(value)
        elif field == "general_level":
            value = GeneralLevel(value)
        current = getattr(config, field)
        current_comparable = current.value if hasattr(current, "value") else current
        if current_comparable == (value.value if hasattr(value, "value") else value):
            continue
        old_value[field] = current_comparable
        new_value[field] = value.value if hasattr(value, "value") else value
        setattr(config, field, value)

    if new_value:
        await audit_service.write_event(
            db,
            tenant_id=tenant_id,
            entity_type=AuditEntityType.project,
            entity_id=project_id,
            action=AuditAction.status_changed,
            performed_by=user_id,
            old_value=old_value,
            new_value=new_value,
        )
    return config


def apply_switching(config: AQLConfig, *, rejected: bool) -> tuple[AQLStatus, AQLStatus]:
    """ISO 2859-1 switching between normal, tightened and reduced inspection,
    driven by the project's AQLConfig thresholds. Updates config in place and
    returns (old_status, new_status). Every switch resets both counters.
    current_status is the *suggested* level for the next lot — the
    supervisor decides each lot's actual level (Lot.inspection_level).

      normal    -> tightened  after failures_to_tighten consecutive rejections
      tightened -> normal     after passes_to_reduce consecutive acceptances
      normal    -> reduced    after passes_to_reduce consecutive acceptances
      reduced   -> normal     on any rejection
    """
    old = config.current_status
    if rejected:
        config.consecutive_passes = 0
        config.consecutive_failures += 1
        if old == AQLStatus.reduced:
            config.current_status = AQLStatus.normal
        elif old == AQLStatus.normal and config.consecutive_failures >= config.failures_to_tighten:
            config.current_status = AQLStatus.tightened
    else:
        config.consecutive_failures = 0
        config.consecutive_passes += 1
        if config.consecutive_passes >= config.passes_to_reduce:
            if old == AQLStatus.tightened:
                config.current_status = AQLStatus.normal
            elif old == AQLStatus.normal:
                config.current_status = AQLStatus.reduced
    if config.current_status != old:
        config.consecutive_passes = 0
        config.consecutive_failures = 0
    return old, config.current_status


async def record_lot_outcome(
    db: AsyncSession,
    *,
    lot,
    passed: bool,
    tenant_id: int,
) -> AQLStatus | None:
    """Feed a lot's customer QC verdict into the project's switching state.
    Only a lot's first QC round counts — a resubmitted lot that fails again
    must not count against the project twice (lot_service.calculate_accuracy
    enforces that). Writes a project audit event when the status switches.
    Returns the new status, or None when the project has no AQLConfig."""
    config = await get_config(db, lot.project_id)
    if config is None:
        return None
    old, new = apply_switching(config, rejected=not passed)
    if new != old:
        await audit_service.write_event(
            db,
            tenant_id=tenant_id,
            entity_type=AuditEntityType.project,
            entity_id=lot.project_id,
            action=AuditAction.batch_escalated if new == AQLStatus.tightened else AuditAction.status_changed,
            performed_by=None,
            old_value={"aql_status": old.value},
            new_value={"aql_status": new.value},
            metadata={"lot_id": lot.id, "lot_passed": passed, "reason": "iso_2859_1_switching_suggestion"},
        )
    return new


async def evaluate_batch(
    db: AsyncSession,
    *,
    project_id: int,
    batch_id: int,
    batch_size: int,
    defects_found: int,
    tenant_id: int,
    performed_by: int,
) -> dict:
    """Evaluate QC result, update AQL state, return outcome."""
    from app.models.batch import Batch, BatchQCResult, BatchStatus

    config = await get_config(db, project_id)
    if config is None:
        raise ValueError("No AQL config for project")

    aql_level = await get_current_aql_level(db, project_id)
    sample_size, acceptance_number = compute_sample_size(batch_size, aql_level)

    outcome = "passed" if defects_found <= acceptance_number else "rejected"

    qc_result = BatchQCResult(
        batch_id=batch_id,
        total_inspected=sample_size,
        defects_found=defects_found,
        acceptance_number=acceptance_number,
        aql_level_applied=aql_level,
        outcome=outcome,
    )
    db.add(qc_result)

    # Update batch status
    batch = await db.get(Batch, batch_id)
    batch.status = BatchStatus.passed if outcome == "passed" else BatchStatus.rejected
    batch.completed_at = datetime.now(timezone.utc)

    old_status = config.current_status.value

    _, new = apply_switching(config, rejected=outcome == "rejected")
    if new == AQLStatus.tightened and old_status != AQLStatus.tightened.value:
        await audit_service.write_event(
            db,
            tenant_id=tenant_id,
            entity_type=AuditEntityType.batch,
            entity_id=batch_id,
            action=AuditAction.batch_escalated,
            performed_by=performed_by,
            old_value={"aql_status": old_status},
            new_value={"aql_status": AQLStatus.tightened.value},
            metadata={"defects_found": defects_found, "acceptance_number": acceptance_number},
        )

    return {
        "outcome": outcome,
        "defects_found": defects_found,
        "acceptance_number": acceptance_number,
        "sample_size": sample_size,
        "aql_level_applied": aql_level,
        "new_aql_status": config.current_status.value,
    }
