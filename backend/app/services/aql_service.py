"""
ISO 2859-1 Acceptance Sampling — Inspection Level II
Sample size code letters and acceptance/rejection numbers.
"""
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import AQLConfig, AQLStatus
from app.models.audit_log import AuditAction, AuditEntityType
from app.services import audit_service

# ISO 2859-1 Level II: batch_size_range -> code_letter
_CODE_LETTER_TABLE = [
    (2, "A"), (8, "B"), (13, "C"), (32, "D"), (50, "E"), (90, "F"),
    (150, "G"), (280, "H"), (500, "J"), (1200, "K"), (3200, "L"),
    (10000, "M"), (35000, "N"), (150000, "P"), (500000, "Q"),
]

# code_letter -> {aql_level -> (sample_size, acceptance_number)}
_SAMPLING_TABLE: dict[str, dict[float, tuple[int, int]]] = {
    "A": {0.65: (2, 0), 1.0: (2, 0), 1.5: (2, 0), 2.5: (2, 0)},
    "B": {0.65: (3, 0), 1.0: (3, 0), 1.5: (3, 0), 2.5: (3, 0)},
    "C": {0.65: (5, 0), 1.0: (5, 0), 1.5: (5, 0), 2.5: (5, 1)},
    "D": {0.65: (8, 0), 1.0: (8, 0), 1.5: (8, 0), 2.5: (8, 0)},
    "E": {0.65: (13, 0), 1.0: (13, 0), 1.5: (13, 0), 2.5: (13, 1)},
    "F": {0.65: (20, 0), 1.0: (20, 0), 1.5: (20, 1), 2.5: (20, 1)},
    "G": {0.65: (32, 0), 1.0: (32, 1), 1.5: (32, 1), 2.5: (32, 2)},
    "H": {0.65: (50, 1), 1.0: (50, 1), 1.5: (50, 2), 2.5: (50, 3)},
    "J": {0.65: (80, 1), 1.0: (80, 2), 1.5: (80, 3), 2.5: (80, 5)},
    "K": {0.65: (125, 2), 1.0: (125, 3), 1.5: (125, 5), 2.5: (125, 7)},
    "L": {0.65: (200, 3), 1.0: (200, 5), 1.5: (200, 7), 2.5: (200, 10)},
    "M": {0.65: (315, 5), 1.0: (315, 7), 1.5: (315, 10), 2.5: (315, 14)},
    "N": {0.65: (500, 7), 1.0: (500, 10), 1.5: (500, 14), 2.5: (500, 21)},
    "P": {0.65: (800, 10), 1.0: (800, 14), 1.5: (800, 21), 2.5: (800, 21)},
    "Q": {0.65: (1250, 14), 1.0: (1250, 21), 1.5: (1250, 21), 2.5: (1250, 21)},
}


def _code_letter(batch_size: int) -> str:
    for max_size, letter in _CODE_LETTER_TABLE:
        if batch_size <= max_size:
            return letter
    return "Q"


def _closest_aql(table: dict[float, tuple[int, int]], aql: float) -> float:
    """Return the nearest AQL key available in the sampling table."""
    return min(table.keys(), key=lambda k: abs(k - aql))


def compute_sample_size(batch_size: int, aql_level: float) -> tuple[int, int]:
    """Return (sample_size, acceptance_number) for given batch size and AQL."""
    letter = _code_letter(batch_size)
    row = _SAMPLING_TABLE[letter]
    actual_aql = _closest_aql(row, aql_level)
    return row[actual_aql]


async def get_current_aql_level(db: AsyncSession, project_id: int) -> float:
    config = await db.get(AQLConfig, project_id)
    if config is None:
        return 1.5
    mapping = {
        AQLStatus.normal: config.normal_aql,
        AQLStatus.tightened: config.tightened_aql,
        AQLStatus.reduced: config.reduced_aql,
    }
    return mapping[config.current_status]


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

    config = await db.get(AQLConfig, project_id)
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

    if outcome == "rejected":
        config.consecutive_passes = 0
        config.consecutive_failures += 1
        if config.current_status != AQLStatus.tightened:
            config.current_status = AQLStatus.tightened
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
    else:
        config.consecutive_failures = 0
        config.consecutive_passes += 1
        if config.current_status == AQLStatus.tightened and config.consecutive_passes >= config.passes_to_reduce:
            config.current_status = AQLStatus.normal
            config.consecutive_passes = 0
        elif config.current_status == AQLStatus.normal and config.consecutive_passes >= config.passes_to_reduce:
            config.current_status = AQLStatus.reduced
            config.consecutive_passes = 0

    return {
        "outcome": outcome,
        "defects_found": defects_found,
        "acceptance_number": acceptance_number,
        "sample_size": sample_size,
        "aql_level_applied": aql_level,
        "new_aql_status": config.current_status.value,
    }
