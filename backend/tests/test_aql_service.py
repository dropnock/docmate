"""Unit tests for ISO 2859-1 AQL sampling logic — no database required — plus
DB-backed tests for the AQLConfig lookup-by-project_id fix and update_config."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AQLConfig, AuditAction, AuditEntityType, AuditLog, Project, S3BucketStatus
from app.services import aql_service
from app.services.aql_service import compute_sample_size


class TestComputeSampleSize:
    def test_small_batch_normal_aql(self):
        # 5 items → code letter A (2-8) → AQL 1.5 → sample 2, accept 0
        sample, accept = compute_sample_size(5, 1.5)
        assert sample == 2
        assert accept == 0

    def test_medium_batch_normal_aql(self):
        # 100 items → code letter F → AQL 1.5 → sample 20, accept 1
        sample, accept = compute_sample_size(100, 1.5)
        assert sample == 20
        assert accept == 1

    def test_large_batch_normal_aql(self):
        # 500 items → code letter H (281-500) → AQL 1.5 → sample 50, accept 2
        sample, accept = compute_sample_size(500, 1.5)
        assert sample == 50
        assert accept == 2

    def test_tightened_aql(self):
        # 100 items → code letter F → AQL 1.0 → sample 20, accept 0
        sample, accept = compute_sample_size(100, 1.0)
        assert sample == 20
        assert accept == 0

    def test_reduced_aql(self):
        # 100 items → code letter F → AQL 2.5 → sample 20, accept 1
        sample, accept = compute_sample_size(100, 2.5)
        assert sample == 20
        assert accept == 1

    def test_batch_at_boundary(self):
        # Exactly at boundary: 90 items → code letter E (51-90)
        sample, accept = compute_sample_size(90, 1.5)
        assert sample == 13

    def test_large_batch_beyond_table(self):
        # 1_000_000 items → capped to code letter Q
        sample, accept = compute_sample_size(1_000_000, 1.5)
        assert sample == 1250

    def test_acceptance_threshold(self):
        # 200 items → code letter G (151-280) → AQL 1.5 → sample 32, accept 1
        sample, accept = compute_sample_size(200, 1.5)
        assert sample == 32
        assert accept == 1

    def test_aql_escalation_tightened_stricter(self):
        # Tightened (1.0) must be at least as strict as normal (1.5) acceptance
        # For the same batch size, tightened acceptance_number <= normal
        _, normal_accept = compute_sample_size(300, 1.5)
        _, tight_accept = compute_sample_size(300, 1.0)
        assert tight_accept <= normal_accept

    def test_aql_reduced_more_lenient(self):
        # Reduced (2.5) must be at least as lenient as normal (1.5)
        _, normal_accept = compute_sample_size(300, 1.5)
        _, reduced_accept = compute_sample_size(300, 2.5)
        assert reduced_accept >= normal_accept


class TestGetConfigLooksUpByProjectId:
    """get_config (and everything that used to call db.get(AQLConfig,
    project_id)) must look up by the project_id column, not by AQLConfig's
    own primary key — the two only coincide by luck in minimally-seeded data."""

    async def test_finds_config_even_when_id_differs_from_project_id(self, db: AsyncSession, seed):
        # `seed` already created one project (id=1) with one AQLConfig
        # (id=1) — aligned by coincidence. Add two more projects, only
        # the second of which gets an AQLConfig row, so that row's id
        # (2 — it's the second AQLConfig ever inserted) ends up not
        # matching its own project_id (3 — the third project inserted).
        project_a = Project(
            tenant_id=seed["tenant"].id, digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id, name="Project A",
            s3_bucket_status=S3BucketStatus.ready,
        )
        project_b = Project(
            tenant_id=seed["tenant"].id, digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id, name="Project B",
            s3_bucket_status=S3BucketStatus.ready,
        )
        db.add_all([project_a, project_b])
        await db.flush()

        config_b = AQLConfig(project_id=project_b.id, normal_aql=9.9, tightened_aql=8.8, reduced_aql=7.7)
        db.add(config_b)
        await db.flush()
        assert config_b.id != project_b.id  # sanity check the misalignment this test relies on

        found = await aql_service.get_config(db, project_b.id)
        assert found is not None
        assert found.project_id == project_b.id
        assert found.normal_aql == 9.9

    async def test_get_current_aql_level_uses_the_right_config(self, db: AsyncSession, seed):
        project_a = Project(
            tenant_id=seed["tenant"].id, digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id, name="Project A",
            s3_bucket_status=S3BucketStatus.ready,
        )
        project_b = Project(
            tenant_id=seed["tenant"].id, digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id, name="Project B",
            s3_bucket_status=S3BucketStatus.ready,
        )
        db.add_all([project_a, project_b])
        await db.flush()
        db.add(AQLConfig(project_id=project_b.id, normal_aql=2.5, tightened_aql=1.0, reduced_aql=1.5))
        await db.flush()

        level = await aql_service.get_current_aql_level(db, project_b.id)
        assert level == 2.5  # config_b's normal_aql, not the 1.5 no-config fallback


class TestUpdateConfig:
    async def test_applies_changed_fields_and_writes_one_audit_event(self, db: AsyncSession, seed):
        updated = await aql_service.update_config(
            db, project_id=seed["project"].id,
            updates={"normal_aql": 2.5, "sampling_mode": "manual"},
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.commit()

        assert updated.normal_aql == 2.5
        assert updated.sampling_mode.value == "manual"

        result = await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.project,
                AuditLog.entity_id == seed["project"].id,
            )
        )
        events = result.scalars().all()
        assert len(events) == 1
        assert events[0].action == AuditAction.status_changed
        assert events[0].new_value == {"normal_aql": 2.5, "sampling_mode": "manual"}
        assert events[0].old_value == {"normal_aql": 1.5, "sampling_mode": "iso"}

    async def test_no_op_update_writes_no_audit_event(self, db: AsyncSession, seed):
        await aql_service.update_config(
            db, project_id=seed["project"].id,
            updates={"normal_aql": seed["aql_config"].normal_aql},
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.commit()
        result = await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.project,
                AuditLog.entity_id == seed["project"].id,
            )
        )
        assert result.scalars().first() is None

    async def test_404_when_no_config_for_project(self, db: AsyncSession, seed):
        project_no_config = Project(
            tenant_id=seed["tenant"].id, digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id, name="No Config Project",
            s3_bucket_status=S3BucketStatus.ready,
        )
        db.add(project_no_config)
        await db.flush()

        with pytest.raises(HTTPException) as exc_info:
            await aql_service.update_config(
                db, project_id=project_no_config.id, updates={"normal_aql": 1.0},
                user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 404
