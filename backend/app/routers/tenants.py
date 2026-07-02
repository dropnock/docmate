from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.organization import Organization, OrgType
from app.models.project import Project, S3BucketStatus
from app.models.tenant import Tenant
from app.schemas.tenant import OrgCreate, OrgOut, ProjectCreate, ProjectOut, ProjectUpdate, TenantCreate, TenantOut

router = APIRouter(prefix="/api", tags=["tenants"])


# ── Tenants ────────────────────────────────────────────────────────────────────

@router.post("/tenants", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_roles("admin")),
):
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Slug already taken")
    tenant = Tenant(name=body.name, slug=body.slug)
    db.add(tenant)
    await db.flush()
    return tenant


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
async def get_tenant(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if current_user.role.value != "admin" and current_user._tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


# ── Organizations ──────────────────────────────────────────────────────────────

@router.post("/organizations", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: OrgCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    import logging
    from app.services.keycloak_service import create_customer_realm, slugify

    logger = logging.getLogger(__name__)
    org_type = OrgType(body.type)

    realm_slug = None
    if org_type == OrgType.customer:
        realm_slug = slugify(body.name)
        try:
            create_customer_realm(realm_slug, body.name)
        except Exception as exc:
            logger.error("Keycloak realm creation failed for %s: %s", realm_slug, exc)
            raise HTTPException(
                status_code=503,
                detail=f"Could not provision Keycloak realm: {exc}",
            )

    org = Organization(
        tenant_id=current_user._tenant_id,
        name=body.name,
        type=org_type,
        realm_slug=realm_slug,
    )
    db.add(org)
    await db.flush()

    if org_type == OrgType.customer:
        from app.services import s3_service
        await s3_service.provision_org_bucket(db, org=org)

    return org


@router.get("/organizations", response_model=list[OrgOut])
async def list_organizations(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = await db.execute(
        select(Organization).where(Organization.tenant_id == current_user._tenant_id)
    )
    return list(result.scalars().all())


# ── Projects ───────────────────────────────────────────────────────────────────

@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin")),
):
    from app.models.aql import AQLConfig
    from app.services import s3_service

    tenant_id = current_user._tenant_id

    # Auto-select the single digitizing entity org for this tenant
    de_result = await db.execute(
        select(Organization).where(
            Organization.tenant_id == tenant_id,
            Organization.type == OrgType.digitizing_entity,
        ).limit(1)
    )
    de_org = de_result.scalar_one_or_none()
    if not de_org:
        raise HTTPException(status_code=400, detail="No digitizing organisation found")

    project = Project(
        tenant_id=tenant_id,
        digitizing_org_id=de_org.id,
        customer_org_id=body.customer_org_id,
        name=body.name,
        description=body.description,
        proposed_end_date=body.proposed_end_date,
        stale_threshold_hours=body.stale_threshold_hours,
        s3_bucket_status=S3BucketStatus.provisioning,
    )
    db.add(project)
    await db.flush()

    # Default AQL config
    aql_config = AQLConfig(project_id=project.id)
    db.add(aql_config)

    # Auto-create the project cabinet, tied to the digitizing organisation
    from app.models.cabinet import Cabinet
    cabinet = Cabinet(
        tenant_id=tenant_id,
        project_id=project.id,
        organization_id=de_org.id,
        name=project.name,
        created_by=current_user.id,
    )
    db.add(cabinet)

    # Provision S3 bucket asynchronously (non-blocking; status updated by s3_service)
    await s3_service.provision_bucket(db, project=project)

    return project


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = await db.execute(
        select(Project).where(Project.tenant_id == current_user._tenant_id)
    )
    return list(result.scalars().all())


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = await db.get(Project, project_id)
    if not project or project.tenant_id != current_user._tenant_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("admin", "de_supervisor")),
):
    project = await db.get(Project, project_id)
    if not project or project.tenant_id != current_user._tenant_id:
        raise HTTPException(status_code=404, detail="Project not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(project, field, value)
    return project
