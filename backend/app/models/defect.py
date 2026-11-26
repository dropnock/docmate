import enum


class ErrorType(str, enum.Enum):
    """What a reviewer found wrong with a field. The project's agreement
    with the customer (AQLConfig.error_classes) maps each to a Severity."""
    missing = "missing"              # value left out
    typo = "typo"                    # typographic error
    interpretation = "interpretation"  # source read or understood wrongly


class Severity(str, enum.Enum):
    """ISO 2859-1 non-conformity class. Each has its own AQL (AQLConfig) and
    so its own Ac/Re; a lot is accepted only when every class is within its
    acceptance number."""
    critical = "critical"
    major = "major"
    minor = "minor"
