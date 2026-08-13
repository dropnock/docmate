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


class AQLConfigUpdate(BaseModel):
    normal_aql: float | None = None
    tightened_aql: float | None = None
    reduced_aql: float | None = None
    passes_to_reduce: int | None = None
    failures_to_tighten: int | None = None
    sampling_mode: Literal["iso", "manual"] | None = None
