"""Shared fixtures for DocMate integration tests.

Uses SQLite in-memory so tests need no running PostgreSQL instance.
Each test gets a fresh database (function-scoped engine).
"""
from datetime import time

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.security as security_module  # noqa — patched below for tests
import app.models  # noqa — register all models with Base
from app.core.database import get_db
from app.main import app
from app.models import (
    AQLConfig,
    AQLStatus,
    Base,
    Batch,
    BatchStatus,
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
from app.models.shift import Shift, ShiftRole, UserProjectAssignment

_SQLITE_URL = "sqlite+aiosqlite:///:memory:"

_TEST_JWT_SECRET = "test-secret"


async def _fake_verify_token(token: str, realm_slug: str) -> dict:
    """Test double for security._verify_token — skips the real JWKS fetch and
    RS256 signature check (no live Keycloak in tests) but trusts the claims
    the test itself embedded via token(), below. All of get_current_user's
    real logic (realm→portal derivation, X-Portal check, DB lookup by
    keycloak_sub) still runs unmodified."""
    return jose_jwt.get_unverified_claims(token)


security_module._verify_token = _fake_verify_token


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
    """Minimal seeded world: tenant → orgs → users → project → batch → record."""
    tenant = Tenant(name="Test Corp", slug="testcorp")
    db.add(tenant)
    await db.flush()

    de_org = Organization(tenant_id=tenant.id, name="DE Org", type=OrgType.digitizing_entity)
    cust_org = Organization(tenant_id=tenant.id, name="Cust Org", type=OrgType.customer)
    db.add_all([de_org, cust_org])
    await db.flush()

    admin = User(
        tenant_id=tenant.id, organization_id=de_org.id, email="admin@test.com",
        keycloak_sub="sub-admin", full_name="Admin",
        role=UserRole.admin, portal=Portal.digitizing, is_active=True,
    )
    supervisor = User(
        tenant_id=tenant.id, organization_id=de_org.id, email="sup@test.com",
        keycloak_sub="sub-supervisor", full_name="Supervisor",
        role=UserRole.de_supervisor, portal=Portal.digitizing, is_active=True,
    )
    indexer = User(
        tenant_id=tenant.id, organization_id=de_org.id, email="indexer@test.com",
        keycloak_sub="sub-indexer", full_name="Indexer One",
        role=UserRole.de_staff, portal=Portal.digitizing, is_active=True,
    )
    indexer2 = User(
        tenant_id=tenant.id, organization_id=de_org.id, email="indexer2@test.com",
        keycloak_sub="sub-indexer2", full_name="Indexer Two",
        role=UserRole.de_staff, portal=Portal.digitizing, is_active=True,
    )
    qa_staff = User(
        tenant_id=tenant.id, organization_id=de_org.id, email="qastaff@test.com",
        keycloak_sub="sub-qastaff", full_name="QA Staff",
        role=UserRole.de_staff, portal=Portal.digitizing, is_active=True,
    )
    qc_agent = User(
        tenant_id=tenant.id, organization_id=cust_org.id, email="qc@test.com",
        keycloak_sub="sub-qcagent", full_name="QC Agent",
        role=UserRole.customer_qc_agent, portal=Portal.customer, is_active=True,
    )
    db.add_all([admin, supervisor, indexer, indexer2, qa_staff, qc_agent])
    await db.flush()

    project = Project(
        tenant_id=tenant.id, digitizing_org_id=de_org.id, customer_org_id=cust_org.id,
        name="Test Project", stale_threshold_hours=8.0,
        s3_bucket_status=S3BucketStatus.ready,
    )
    db.add(project)
    await db.flush()

    shift = Shift(
        tenant_id=tenant.id, name="Day Shift",
        start_time=time(9, 0), end_time=time(17, 0),
    )
    db.add(shift)
    await db.flush()

    db.add_all([
        UserProjectAssignment(
            user_id=indexer.id, project_id=project.id, shift_id=shift.id,
            shift_role=ShiftRole.indexer, is_active=True,
        ),
        UserProjectAssignment(
            user_id=indexer2.id, project_id=project.id, shift_id=shift.id,
            shift_role=ShiftRole.indexer, is_active=True,
        ),
        UserProjectAssignment(
            user_id=qa_staff.id, project_id=project.id, shift_id=shift.id,
            shift_role=ShiftRole.qa, is_active=True,
        ),
    ])
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
        "indexer": indexer, "indexer2": indexer2, "qa_staff": qa_staff, "qc_agent": qc_agent,
        "project": project, "shift": shift, "aql_config": aql_config, "doc_type": doc_type,
        "batch": batch, "record": record, "record2": record2,
    }


def token(user: User, portal_override: str | None = None) -> str:
    """Builds a JWT-shaped token with the claims get_current_user actually reads
    (sub, iss) — portal is derived from the realm in `iss`, exactly as in prod.
    Signature is not checked in tests (see _fake_verify_token above)."""
    portal = portal_override or user.portal.value
    realm_slug = "doc" if portal == "digitizing" else "customer-test-realm"
    claims = {"sub": user.keycloak_sub, "iss": f"http://keycloak.local/realms/{realm_slug}"}
    return jose_jwt.encode(claims, _TEST_JWT_SECRET, algorithm="HS256")


@pytest_asyncio.fixture
async def client(db: AsyncSession):
    """AsyncClient wired to the test DB via dependency override."""
    async def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)
