from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.aql import AQLConfig
from app.services.aql_service import compute_sample_size, get_current_aql_level

router = APIRouter(prefix="/api", tags=["aql"])


@router.get("/projects/{project_id}/aql")
async def get_aql_status(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    config = await db.get(AQLConfig, project_id)
    if not config:
        return {"status": "not_configured"}
    current_level = await get_current_aql_level(db, project_id)
    return {
        "current_status": config.current_status.value,
        "current_aql_level": current_level,
        "consecutive_passes": config.consecutive_passes,
        "consecutive_failures": config.consecutive_failures,
        "normal_aql": config.normal_aql,
        "tightened_aql": config.tightened_aql,
        "reduced_aql": config.reduced_aql,
    }


@router.get("/aql/sample-size")
async def calc_sample_size(
    batch_size: int,
    aql_level: float = 1.5,
    current_user=Depends(get_current_user),
):
    size, acceptance = compute_sample_size(batch_size, aql_level)
    return {"sample_size": size, "acceptance_number": acceptance, "aql_level": aql_level}
