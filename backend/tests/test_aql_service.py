"""Unit tests for ISO 2859-1 AQL sampling logic — no database required — plus
DB-backed tests for the AQLConfig lookup-by-project_id fix and update_config."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import AQLStatus, GeneralLevel
from app.models import AQLConfig, AuditAction, AuditEntityType, AuditLog, Project, S3BucketStatus
from app.services import aql_service
from app.services.aql_service import compute_sample_size


N, T, R = AQLStatus.normal, AQLStatus.tightened, AQLStatus.reduced


class TestComputeSampleSize:
    """Cells pinned from ISO 2859-1 Tables 2-A/2-B/2-C (single sampling,
    general inspection level II). An arrow cell takes the plan — sample
    size included — of the row it points to."""

    def test_small_lot_follows_arrow_down(self):
        # 5 items -> A; AQL 1.5 column is an arrow down to D: n=8, Ac 0
        # (sampling_plan caps n at the lot -> 100% inspection).
        assert compute_sample_size(5, 1.5) == (8, 0)

    def test_arrow_down_to_next_plan(self):
        # 100 items -> F; AQL 1.5 is an arrow down to G: n=32, Ac 1.
        assert compute_sample_size(100, 1.5) == (32, 1)

    def test_arrow_up_to_previous_plan(self):
        # 100 items -> F; AQL 1.0 is an arrow up to E: n=13, Ac 0.
        assert compute_sample_size(100, 1.0) == (13, 0)
        # 90 items -> E; AQL 1.5 is an arrow up to D: n=8, Ac 0.
        assert compute_sample_size(90, 1.5) == (8, 0)

    def test_plain_cells(self):
        assert compute_sample_size(100, 2.5) == (20, 1)    # F
        assert compute_sample_size(200, 1.5) == (32, 1)    # G
        assert compute_sample_size(500, 1.5) == (50, 2)    # H
        assert compute_sample_size(1000, 2.5) == (80, 5)   # J
        assert compute_sample_size(2000, 2.5) == (125, 7)  # K

    def test_large_lot_beyond_table(self):
        # 1_000_000 items -> Q; AQL 1.5 is an arrow up to P: n=800, Ac 21.
        assert compute_sample_size(1_000_000, 1.5) == (800, 21)

    def test_untabulated_aql_snaps_to_nearest_column(self):
        assert compute_sample_size(500, 1.4) == compute_sample_size(500, 1.5)

    def test_tightened_cells(self):
        assert compute_sample_size(2000, 2.5, T) == (125, 5)  # K: 7 -> 5
        assert compute_sample_size(1000, 1.0, T) == (80, 1)   # J: 2 -> 1
        assert compute_sample_size(5000, 0.65, T) == (200, 2)  # L: 3 -> 2

    def test_reduced_cells(self):
        # Reduced K draws n=50 (normal H's sample) with H's Ac at that AQL.
        assert compute_sample_size(2000, 2.5, R) == (50, 3)
        assert compute_sample_size(1000, 1.5, R) == (32, 1)  # J
        assert compute_sample_size(5000, 1.5, R) == (80, 3)  # L

    @pytest.mark.parametrize("aql", aql_service.AVAILABLE_AQLS)
    @pytest.mark.parametrize("lot_size", [2, 9, 16, 26, 51, 91, 151, 281, 501, 1201, 3201, 10001, 35001, 150001, 500001])
    def test_levels_order_strictness(self, lot_size, aql):
        """Reduced never samples more than normal; tightened never accepts
        more non-conformities than normal at the same sample size, and never
        samples fewer records with a looser Ac."""
        n_n, ac_n = compute_sample_size(lot_size, aql, N)
        n_t, ac_t = compute_sample_size(lot_size, aql, T)
        n_r, _ = compute_sample_size(lot_size, aql, R)
        assert min(n_r, lot_size) <= min(n_n, lot_size)
        if n_t == n_n:
            assert ac_t <= ac_n
        assert not (n_t <= n_n and ac_t > ac_n)


class TestSamplingPlanLevels:
    def test_plans_by_level_for_a_250_record_lot(self):
        # The example where tightening used to change nothing: G at AQL 1.5.
        plans = aql_service.plans_by_level(250, 1.5)
        assert plans["normal"] == {"code_letter": "G", "sample_size": 32, "acceptance_number": 1, "rejection_number": 2}
        assert plans["tightened"] == {"code_letter": "G", "sample_size": 50, "acceptance_number": 1, "rejection_number": 2}
        assert plans["reduced"] == {"code_letter": "G", "sample_size": 8, "acceptance_number": 0, "rejection_number": 1}

    def test_sample_capped_at_lot_size_for_every_level(self):
        for level in (N, T, R):
            assert aql_service.sampling_plan(20, 1.5, level)["sample_size"] <= 20


class TestGeneralInspectionLevel:
    """ISO 2859-1 Table 1: lot size -> code letter for general levels I/II/III."""

    @pytest.mark.parametrize("lot_size,i,ii,iii", [
        (2, "A", "A", "B"), (8, "A", "A", "B"), (9, "A", "B", "C"), (15, "A", "B", "C"),
        (16, "B", "C", "D"), (26, "C", "D", "E"), (51, "C", "E", "F"), (91, "D", "F", "G"),
        (151, "E", "G", "H"), (281, "F", "H", "J"), (501, "G", "J", "K"), (1000, "G", "J", "K"),
        (1200, "G", "J", "K"), (1201, "H", "K", "L"), (3000, "H", "K", "L"), (3201, "J", "L", "M"),
        (10001, "K", "M", "N"), (35001, "L", "N", "P"), (150001, "M", "P", "Q"), (500001, "N", "Q", "R"),
    ])
    def test_code_letters(self, lot_size, i, ii, iii):
        letters = {
            lvl: aql_service.sampling_plan(lot_size, 1.5, N, lvl)["code_letter"]
            for lvl in GeneralLevel
        }
        assert letters == {GeneralLevel.I: i, GeneralLevel.II: ii, GeneralLevel.III: iii}

    def test_default_is_level_ii(self):
        assert aql_service.sampling_plan(1000, 1.5, T) == aql_service.sampling_plan(1000, 1.5, T, GeneralLevel.II)

    def test_tightened_1000_and_3000_records_at_aql_1_5(self):
        def plan(size, lvl):
            p = aql_service.sampling_plan(size, 1.5, T, lvl)
            return p["code_letter"], p["sample_size"], p["acceptance_number"], p["rejection_number"]
        assert plan(1000, GeneralLevel.II) == ("J", 80, 2, 3)
        assert plan(3000, GeneralLevel.II) == ("K", 125, 3, 4)
        assert plan(1000, GeneralLevel.III) == ("K", 125, 3, 4)
        assert plan(3000, GeneralLevel.III) == ("L", 200, 5, 6)

    def test_largest_level_iii_lot_resolves_for_every_table(self):
        for level in (N, T, R):
            for aql in aql_service.AVAILABLE_AQLS:
                assert aql_service.sampling_plan(1_000_000, aql, level, GeneralLevel.III)["sample_size"] > 0

    def test_plans_by_level_uses_general_level(self):
        plans = aql_service.plans_by_level(1000, 1.5, GeneralLevel.III)
        assert {lvl: p["code_letter"] for lvl, p in plans.items()} == {
            "normal": "K", "tightened": "K", "reduced": "K",
        }
        assert plans["normal"]["sample_size"] == 125


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


class TestSuggestedLevel:
    async def test_suggested_level_is_the_switching_state(self, db: AsyncSession, seed):
        assert await aql_service.get_suggested_level(db, seed["project"].id) == N
        seed["aql_config"].current_status = T
        await db.flush()
        assert await aql_service.get_suggested_level(db, seed["project"].id) == T
        # The AQL itself doesn't move with the level.
        assert await aql_service.get_current_aql_level(db, seed["project"].id) == seed["aql_config"].normal_aql


class TestUpdateConfig:
    async def test_rejects_untabulated_aql(self, db: AsyncSession, seed):
        with pytest.raises(HTTPException) as exc_info:
            await aql_service.update_config(
                db, project_id=seed["project"].id, updates={"normal_aql": 2.0},
                user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc_info.value.status_code == 422

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

    async def test_general_level_update_is_audited(self, db: AsyncSession, seed):
        assert await aql_service.get_general_level(db, seed["project"].id) == GeneralLevel.II
        await aql_service.update_config(
            db, project_id=seed["project"].id, updates={"general_level": "III"},
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.commit()
        assert await aql_service.get_general_level(db, seed["project"].id) == GeneralLevel.III
        event = (await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.project, AuditLog.entity_id == seed["project"].id,
            )
        )).scalars().one()
        assert (event.old_value, event.new_value) == ({"general_level": "II"}, {"general_level": "III"})

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
