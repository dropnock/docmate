from typing import Literal

from pydantic import BaseModel


class AQLConfigOut(BaseModel):
    current_status: str
    current_aql_level: float
    consecutive_passes: int
    consecutive_failures: int
    normal_aql: float
    tightened_aql: float
    reduced_aql: float
    passes_to_reduce: int
    failures_to_tighten: int
    sampling_mode: str
    general_level: str
    # Per-class AQLs (critical None = zero tolerance) and the error type ->
    # class agreement; see defect_class_service.
    critical_aql: float | None = None
    major_aql: float
    minor_aql: float
    error_classes: dict[str, str]


class AQLConfigUpdate(BaseModel):
    normal_aql: float | None = None
    tightened_aql: float | None = None
    reduced_aql: float | None = None
    passes_to_reduce: int | None = None
    failures_to_tighten: int | None = None
    sampling_mode: Literal["iso", "manual"] | None = None
    # ISO 2859-1 general inspection level (Table 1 column); II by default.
    general_level: Literal["I", "II", "III"] | None = None
    # Send critical_aql: null for zero tolerance.
    critical_aql: float | None = None
    major_aql: float | None = None
    minor_aql: float | None = None
    error_classes: dict[str, Literal["major", "minor"]] | None = None
