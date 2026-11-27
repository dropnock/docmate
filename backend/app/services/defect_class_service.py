"""Classifying non-conformities and judging a sample against per-class
Ac/Re — the one rule both the digitizing supervisor's outgoing inspection
(lot_inspection_service) and the customer's QC (lot_service) use.

- An error on an identifier field ("x-critical": true in the document
  type's schema) is critical, whatever kind of error it is.
- Any other field's error takes the class the project's agreement with the
  customer gives its error type (AQLConfig.error_classes; by default missing
  data is major, typographic and interpretation errors minor).
- A mark with no error type (made before error types existed) counts as
  major on an ordinary field.
- Each defective field is one non-conformity. The sample's non-conformities
  are added up per class; the lot is accepted only when every class's total
  is at or below its acceptance number (aql_service.class_plans).
"""
from typing import Any, Iterable

from fastapi import HTTPException

from app.models.defect import ErrorType, Severity

DEFAULT_ERROR_CLASSES: dict[str, str] = {
    ErrorType.missing.value: Severity.major.value,
    ErrorType.typo.value: Severity.minor.value,
    ErrorType.interpretation.value: Severity.minor.value,
}

ERROR_TYPE_LABELS: dict[str, str] = {
    ErrorType.missing.value: "Missing data",
    ErrorType.typo.value: "Typographic error",
    ErrorType.interpretation.value: "Interpretation error",
}


def validate_error_classes(value: Any) -> dict[str, str]:
    """A complete error type -> class map: every error type, each mapped to
    major or minor (critical is reserved for identifier fields). Missing
    entries take the default."""
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="error_classes must map each error type to a class")
    unknown = set(value) - set(DEFAULT_ERROR_CLASSES)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown error type(s): {sorted(unknown)}")
    out = dict(DEFAULT_ERROR_CLASSES)
    for error_type, severity in value.items():
        if severity not in (Severity.major.value, Severity.minor.value):
            raise HTTPException(
                status_code=422,
                detail=f"{error_type} must be major or minor (critical is for identifier fields)",
            )
        out[error_type] = severity
    return out


def error_classes_of(config) -> dict[str, str]:
    """The project's agreement, or the defaults when it has no AQL config."""
    stored = getattr(config, "error_classes", None) if config is not None else None
    return {**DEFAULT_ERROR_CLASSES, **(stored or {})}


async def project_error_classes(db, project_id: int) -> dict[str, str]:
    from app.services import aql_service
    return error_classes_of(await aql_service.get_config(db, project_id))


def mark_class(fr, *, is_identifier: bool, error_classes: dict[str, str]) -> tuple[str | None, Severity | None]:
    """(error_type, severity) to store for one submitted field mark: both
    None for an accepted field."""
    if fr.status != "defective":
        return None, None
    error_type = getattr(fr, "error_type", None)
    return error_type, classify(is_identifier=is_identifier, error_type=error_type, error_classes=error_classes)


def classify(*, is_identifier: bool, error_type: ErrorType | str | None, error_classes: dict[str, str]) -> Severity:
    if is_identifier:
        return Severity.critical
    if error_type is None:
        return Severity.major
    return Severity(error_classes.get(ErrorType(error_type).value, Severity.major.value))


def tally(severities: Iterable[Severity | str]) -> dict[str, int]:
    counts = {s.value: 0 for s in Severity}
    for s in severities:
        counts[Severity(s).value] += 1
    return counts


def judge(counts: dict[str, int], plan_classes: dict[str, dict]) -> dict[str, Any]:
    """Compare per-class non-conformity totals with the plan's Ac/Re.
    Returns {"accepted": bool, "classes": {cls: {aql, acceptance_number,
    rejection_number, found, accepted}}}."""
    classes = {}
    for cls in (s.value for s in Severity):
        plan = plan_classes[cls]
        found = counts.get(cls, 0)
        classes[cls] = {**plan, "found": found, "accepted": found <= plan["acceptance_number"]}
    return {"accepted": all(c["accepted"] for c in classes.values()), "classes": classes}


def effective(qfr, *, overrides: bool = True) -> tuple[str, ErrorType | None, Severity | None]:
    """(status, error_type, severity) a QcFieldResult counts as: the customer
    QC supervisor's override when there is one (lot_service.override_qc_mark),
    otherwise the reviewer's own mark (overrides=False: always the
    reviewer's). A defect with no stored class (marks made before classes
    existed) counts as critical on an identifier field and major otherwise."""
    if overrides and qfr.override_status is not None:
        status = getattr(qfr.override_status, "value", qfr.override_status)
        error_type, severity = qfr.override_error_type, qfr.override_severity
    else:
        status = getattr(qfr.status, "value", qfr.status)
        error_type, severity = qfr.error_type, qfr.severity
    if status != "defective":
        return status, None, None
    if severity is None:
        severity = Severity.critical if qfr.is_critical else Severity.major
    return status, error_type, Severity(severity)
