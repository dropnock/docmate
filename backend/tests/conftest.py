"""Shared fixtures for DocMate integration tests.

Uses SQLite in-memory so tests need no running PostgreSQL or Keycloak instance.
Each test gets a fresh database (function-scoped engine). Authentication is
bypassed via FastAPI dependency_overrides — get_current_user returns the seeded
user based on a test-only bearer token "test:<user_id>".
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa — register all models with Base
from app.core.database import get_db
from app.core.security import get_current_user
from app.main import app
from app.models import (
    AQLConfig,
    AQLStatus,
    Base,
    Batch,
    BatchStatus,
    Cabinet,
    DocumentType,
    Organization,
    OrgType,
    Portal,
    Project,
    Record,
    RecordStatus,
    S3BucketStatus,
    Tenant,
    User,
    UserRole,
)

import pytest_asyncio
from fastapi import HTTPException, Request
from httpx import AsyncClient, ASGITransport

_SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def auth_headers(user: User) -> dict:
    """Return Authorization headers that authenticate as the given user in tests."""
    return {"Authorization": f"Bearer test:{user.id}"}


@pytest_asyncio.fixture
async def db():
    """Fresh in-memory SQLite per test with all tables created."""
    engine = create_async_engine(_SQLITE_URL, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def seed(db: AsyncSession):
    """Minimal seeded world: tenant → orgs → users → project → cabinet → batch → records."""
    tenant = Tenant(name="Test Corp", slug="testcorp")
    db.add(tenant)
    await db.flush()

    de_org = Organization(tenant_id=tenant.id, name="DE Org", type=OrgType.digitizing_entity)
    cust_org = Organization(
        tenant_id=tenant.id, name="Cust Org", type=OrgType.customer,
        realm_slug="cust-realm",
    )
    db.add_all([de_org, cust_org])
    await db.flush()

    admin = User(
        tenant_id=tenant.id, organization_id=de_org.id, email="admin@test.com",
        full_name="Admin", role=UserRole.admin, portal=Portal.digitizing, is_active=True,
    )
    supervisor = User(
        tenant_id=tenant.id, organization_id=de_org.id, email="sup@test.com",
        full_name="Supervisor", role=UserRole.de_supervisor,
        portal=Portal.digitizing, is_active=True,
    )
    indexer = User(
        tenant_id=tenant.id, organization_id=de_org.id, email="indexer@test.com",
        full_name="Indexer One", role=UserRole.de_indexer,
        portal=Portal.digitizing, is_active=True,
    )
    indexer2 = User(
        tenant_id=tenant.id, organization_id=de_org.id, email="indexer2@test.com",
        full_name="Indexer Two", role=UserRole.de_indexer,
        portal=Portal.digitizing, is_active=True,
    )
    qc_agent = User(
        tenant_id=tenant.id, organization_id=cust_org.id, email="qc@test.com",
        full_name="QC Agent", role=UserRole.customer_qc_agent,
        portal=Portal.customer, is_active=True,
    )
    cust_supervisor = User(
        tenant_id=tenant.id, organization_id=cust_org.id, email="custsup@test.com",
        full_name="Cust Supervisor", role=UserRole.customer_supervisor,
        portal=Portal.customer, is_active=True,
    )
    db.add_all([admin, supervisor, indexer, indexer2, qc_agent, cust_supervisor])
    await db.flush()

    project = Project(
        tenant_id=tenant.id, digitizing_org_id=de_org.id, customer_org_id=cust_org.id,
        name="Test Project", stale_threshold_hours=8.0,
        s3_bucket_name="docmate-testcorp-test-project",
        s3_bucket_status=S3BucketStatus.ready,
    )
    db.add(project)
    await db.flush()

    aql_config = AQLConfig(
        project_id=project.id, current_status=AQLStatus.normal,
        consecutive_passes=0, consecutive_failures=0,
        normal_aql=1.5, tightened_aql=1.0, reduced_aql=2.5,
        passes_to_reduce=5, failures_to_tighten=1,
    )
    db.add(aql_config)

    doc_type = DocumentType(
        project_id=project.id, name="Form A",
        json_schema={"fields": [{"name": "title", "type": "string"}]},
    )
    db.add(doc_type)
    await db.flush()

    cabinet = Cabinet(
        tenant_id=tenant.id, project_id=project.id,
        organization_id=de_org.id, name="Test Cabinet",
        created_by=admin.id,
    )
    db.add(cabinet)
    await db.flush()

    batch = Batch(
        project_id=project.id, document_type_id=doc_type.id,
        name="Batch 001", status=BatchStatus.indexing,
    )
    db.add(batch)
    await db.flush()

    record = Record(batch_id=batch.id, status=RecordStatus.pending, current_version=1)
    record2 = Record(batch_id=batch.id, status=RecordStatus.pending, current_version=1)
    db.add_all([record, record2])
    await db.flush()
    await db.commit()

    return {
        "tenant": tenant, "de_org": de_org, "cust_org": cust_org,
        "admin": admin, "supervisor": supervisor,
        "indexer": indexer, "indexer2": indexer2,
        "qc_agent": qc_agent, "cust_supervisor": cust_supervisor,
        "project": project, "aql_config": aql_config, "doc_type": doc_type,
        "cabinet": cabinet, "batch": batch, "record": record, "record2": record2,
    }


@pytest_asyncio.fixture
async def client(db: AsyncSession):
    """AsyncClient wired to test DB with Keycloak auth bypassed."""
    async def _override_get_db():
        yield db

    def _make_auth_override(session: AsyncSession):
        async def _inner(request: Request):
            auth = request.headers.get("Authorization", "")
            prefix = "Bearer test:"
            if not auth.startswith(prefix):
                raise HTTPException(status_code=401, detail="Not authenticated")
            try:
                uid = int(auth[len(prefix):])
            except ValueError:
                raise HTTPException(status_code=401, detail="Invalid test token")
            result = await session.execute(select(User).where(User.id == uid))
            user = result.scalar_one_or_none()
            if not user or not user.is_active:
                raise HTTPException(status_code=401, detail="User not found or inactive")
            user._tenant_id = user.tenant_id
            return user
        return _inner

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _make_auth_override(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)
