from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import check_project_access, get_current_user, require_roles
from app.models.project import Project
from app.schemas.aql import AQLConfigOut, AQLConfigUpdate
from app.services import aql_service
from app.services.aql_service import compute_sample_size, get_current_aql_level

router = APIRouter(prefix="/api", tags=["aql"])


@router.get("/projects/{project_id}/aql", response_model=AQLConfigOut | dict)
async def get_aql_status(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = await db.get(Project, project_id)
    check_project_access(project, current_user)
    config = await aql_service.get_config(db, project_id)
    if not config:
        return {"status": "not_configured"}
    current_level = await get_current_aql_level(db, project_id)
    return AQLConfigOut(
        current_status=config.current_status.value,
        current_aql_level=current_level,
        consecutive_passes=config.consecutive_passes,
        consecutive_failures=config.consecutive_failures,
        normal_aql=config.normal_aql,
        tightened_aql=config.tightened_aql,
        reduced_aql=config.reduced_aql,
        passes_to_reduce=config.passes_to_reduce,
        failures_to_tighten=config.failures_to_tighten,
        sampling_mode=config.sampling_mode.value,
    )


@router.patch("/projects/{project_id}/aql", response_model=AQLConfigOut)
async def update_aql_config(
    project_id: int,
    body: AQLConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("customer_supervisor", "admin")),
):
    project = await db.get(Project, project_id)
    check_project_access(project, current_user)
    config = await aql_service.update_config(
        db,
        project_id=project_id,
        updates=body.model_dump(exclude_unset=True),
        user_id=current_user.id,
        tenant_id=current_user._tenant_id,
    )
    await db.commit()
    current_level = await get_current_aql_level(db, project_id)
    return AQLConfigOut(
        current_status=config.current_status.value,
        current_aql_level=current_level,
        consecutive_passes=config.consecutive_passes,
        consecutive_failures=config.consecutive_failures,
        normal_aql=config.normal_aql,
        tightened_aql=config.tightened_aql,
        reduced_aql=config.reduced_aql,
        passes_to_reduce=config.passes_to_reduce,
        failures_to_tighten=config.failures_to_tighten,
        sampling_mode=config.sampling_mode.value,
    )


@router.get("/aql/sample-size")
async def calc_sample_size(
    batch_size: int,
    aql_level: float = 1.5,
    current_user=Depends(get_current_user),
):
    size, acceptance = compute_sample_size(batch_size, aql_level)
    return {"sample_size": size, "acceptance_number": acceptance, "aql_level": aql_level}
