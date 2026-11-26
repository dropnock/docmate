"""Critical / major / minor non-conformity classes: the per-class sampling
plan (aql_service.class_plans), classification of a defective field
(defect_class_service), the project settings, and the class stored with
each field mark."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import AQLStatus, GeneralLevel
from app.models.defect import ErrorType, Severity
from app.models.lot_inspection import LotInspectionFieldResult
from app.models.qc_field_result import QcFieldResult
from app.schemas.task import FieldResultIn
from app.services import aql_service, defect_class_service, lot_inspection_service, task_service
from app.services.aql_service import AVAILABLE_AQLS, class_plans, compute_sample_size, sampling_plan
from tests.conftest import token
from tests.test_aql_router import _make_customer_supervisor
from tests.test_lot_inspections import _items, _make_lot, _start
from tests.test_qc_field_results import _make_qc_setup

LEVELS = (AQLStatus.normal, AQLStatus.tightened, AQLStatus.reduced)


def _classes(plan):
    return {c: (v["acceptance_number"], v["rejection_number"]) for c, v in plan["classes"].items()}


class TestNewAqlColumns:
    """ISO 2859-1 Table 2-A / 2-B cells for AQL 4.0 and 6.5."""

    @pytest.mark.parametrize("lot_size, aql, n, ac", [
        (500, 4.0, 50, 5), (500, 6.5, 50, 7),        # H
        (1000, 4.0, 80, 7), (1000, 6.5, 80, 10),     # J
        (3000, 4.0, 125, 10), (3000, 6.5, 125, 14),  # K
        (2, 6.5, 2, 0),                              # A: 6.5 has a plan at n=2
        (2, 4.0, 3, 0),                              # A: 4.0 arrow down to B
        (10, 6.5, 2, 0),                             # B: arrow up to A
        (20, 6.5, 8, 1),                             # C: arrow down to D
        (200_000, 4.0, 315, 21),                     # P: arrow up to M
    ])
    def test_normal(self, lot_size, aql, n, ac):
        assert compute_sample_size(lot_size, aql) == (n, ac)

    @pytest.mark.parametrize("lot_size, aql, n, ac", [
        (500, 4.0, 50, 3), (500, 6.5, 50, 5),
        (1000, 4.0, 80, 5), (1000, 6.5, 80, 8),
        (200, 4.0, 32, 2),                           # G
        (60, 4.0, 20, 1),                            # E: arrow down to F
    ])
    def test_tightened(self, lot_size, aql, n, ac):
        assert compute_sample_size(lot_size, aql, AQLStatus.tightened) == (n, ac)


class TestClassPlans:
    @pytest.mark.parametrize("level", LEVELS)
    @pytest.mark.parametrize("aql", AVAILABLE_AQLS)
    @pytest.mark.parametrize("lot_size", [2, 9, 20, 40, 90, 150, 280, 500, 1200, 3200, 10_000, 600_000])
    def test_one_aql_gives_the_ordinary_plan(self, lot_size, aql, level):
        """Every class at the same AQL, or one class with the others zero
        tolerance... the AQL'd class matches sampling_plan exactly."""
        single = sampling_plan(lot_size, aql, level)
        plan = class_plans(lot_size, {"critical": None, "major": aql, "minor": aql}, level)
        assert plan["sample_size"] == single["sample_size"]
        assert plan["classes"]["major"]["acceptance_number"] == single["acceptance_number"]
        assert plan["classes"]["minor"]["acceptance_number"] == single["acceptance_number"]
        assert _classes(plan)["critical"] == (0, 1)

    def test_shared_row(self):
        # 500 records -> H (n=50): every class has a plan at H.
        plan = class_plans(500, {"critical": None, "major": 1.0, "minor": 4.0})
        assert plan["code_letter"] == plan["plan_letter"] == "H"
        assert plan["sample_size"] == 50
        assert _classes(plan) == {"critical": (0, 1), "major": (1, 2), "minor": (5, 6)}

    def test_largest_sample_wins_and_others_read_at_that_size(self):
        """90 records -> E (n=13). Critical 0.65 needs F (n=20, Ac 0); major
        1.5 points up to D (n=8, Ac 0); minor 4.0 has E (Ac 1). One sample
        of 20: major has no plan at n=20 (arrow), so it keeps its largest
        plan below (Ac 0); minor 4.0 at F is Ac 2."""
        plan = class_plans(90, {"critical": 0.65, "major": 1.5, "minor": 4.0})
        assert plan["code_letter"] == "E" and plan["plan_letter"] == "F"
        assert plan["sample_size"] == 20
        assert _classes(plan) == {"critical": (0, 1), "major": (0, 1), "minor": (2, 3)}

    def test_reading_at_a_larger_sample_is_never_more_lenient_than_own_plan(self):
        for level in LEVELS:
            for lot_size in (5, 20, 60, 100, 400, 2000, 20_000):
                for major in AVAILABLE_AQLS:
                    plan = class_plans(lot_size, {"critical": 0.65, "major": major, "minor": 6.5}, level)
                    own = sampling_plan(lot_size, major, level)
                    assert plan["sample_size"] >= own["sample_size"]
                    assert plan["classes"]["major"]["acceptance_number"] >= own["acceptance_number"]

    def test_all_zero_tolerance_uses_the_lots_letter(self):
        plan = class_plans(500, {"critical": None, "major": None, "minor": None})
        assert plan["sample_size"] == 50 and _classes(plan)["major"] == (0, 1)

    def test_capped_at_lot_size(self):
        assert class_plans(3, {"critical": 0.65, "major": 1.0, "minor": 2.5})["sample_size"] == 3

    def test_general_level(self):
        plan = class_plans(500, {"critical": None, "major": 1.0, "minor": 4.0}, general_level=GeneralLevel.III)
        assert plan["code_letter"] == "J" and plan["sample_size"] == 80

    def test_rejects_untabulated_aql(self):
        with pytest.raises(HTTPException) as e:
            class_plans(500, {"critical": None, "major": 1.2, "minor": 4.0})
        assert e.value.status_code == 422


class TestClassify:
    ec = defect_class_service.DEFAULT_ERROR_CLASSES

    def test_identifier_field_is_always_critical(self):
        for et in [*ErrorType, None]:
            assert defect_class_service.classify(is_identifier=True, error_type=et, error_classes=self.ec) == Severity.critical

    def test_default_agreement(self):
        c = lambda et: defect_class_service.classify(is_identifier=False, error_type=et, error_classes=self.ec)
        assert c("missing") == Severity.major
        assert c("typo") == Severity.minor
        assert c("interpretation") == Severity.minor
        assert c(None) == Severity.major  # marks from before error types

    def test_project_agreement(self):
        ec = {**self.ec, "interpretation": "major"}
        assert defect_class_service.classify(is_identifier=False, error_type="interpretation", error_classes=ec) == Severity.major

    def test_validate_fills_defaults_and_refuses_critical(self):
        assert defect_class_service.validate_error_classes({"typo": "major"}) == {
            "missing": "major", "typo": "major", "interpretation": "minor",
        }
        for bad in ({"typo": "critical"}, {"smudge": "minor"}, ["typo"]):
            with pytest.raises(HTTPException):
                defect_class_service.validate_error_classes(bad)

    def test_judge_needs_every_class_within_ac(self):
        plan = class_plans(500, {"critical": None, "major": 1.0, "minor": 4.0})["classes"]
        ok = defect_class_service.judge({"critical": 0, "major": 1, "minor": 5}, plan)
        assert ok["accepted"]
        for counts in ({"critical": 1}, {"major": 2}, {"minor": 6}):
            assert not defect_class_service.judge(counts, plan)["accepted"]
        bad = defect_class_service.judge({"minor": 6}, plan)["classes"]
        assert bad["minor"] == {"aql": 4.0, "acceptance_number": 5, "rejection_number": 6, "found": 6, "accepted": False}

    def test_tally(self):
        assert defect_class_service.tally(["minor", Severity.minor, "critical"]) == {"critical": 1, "major": 0, "minor": 2}


class TestSettings:
    async def test_major_aql_is_normal_aql(self, db: AsyncSession, seed):
        cfg = await aql_service.update_config(
            db, project_id=seed["project"].id,
            updates={"major_aql": 1.0, "minor_aql": 6.5, "critical_aql": 0.65},
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert (cfg.normal_aql, cfg.minor_aql, cfg.critical_aql) == (1.0, 6.5, 0.65)
        assert aql_service.config_class_aqls(cfg) == {"critical": 0.65, "major": 1.0, "minor": 6.5}
        assert await aql_service.get_current_aql_level(db, seed["project"].id) == 1.0

        await aql_service.update_config(
            db, project_id=seed["project"].id, updates={"critical_aql": None},
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert cfg.critical_aql is None

    @pytest.mark.parametrize("updates", [{"major_aql": None}, {"minor_aql": 3.0}, {"error_classes": {"typo": "critical"}}])
    async def test_rejects(self, db: AsyncSession, seed, updates):
        with pytest.raises(HTTPException) as e:
            await aql_service.update_config(
                db, project_id=seed["project"].id, updates=updates,
                user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert e.value.status_code == 422

    async def test_api(self, db: AsyncSession, seed, client):
        sup = await _make_customer_supervisor(db, seed)
        headers = {"Authorization": f"Bearer {token(sup, portal_override='customer')}", "X-Portal": "customer"}
        resp = await client.patch(
            f"/api/projects/{seed['project'].id}/aql",
            json={"major_aql": 1.0, "minor_aql": 4.0, "error_classes": {"interpretation": "major"}},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["major_aql"], body["minor_aql"], body["critical_aql"]) == (1.0, 4.0, None)
        assert body["error_classes"] == {"missing": "major", "typo": "minor", "interpretation": "major"}

        resp = await client.get(
            "/api/aql/class-plan", params={"lot_size": 90, "critical_aql": 0.65, "major_aql": 1.5, "minor_aql": 4.0},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["sample_size"] == 20


class TestMarksStoreTheirClass:
    async def test_outgoing_inspection(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=5)
        inspection = await _start(db, seed, lot)
        item = (await _items(db, inspection))[0]
        await lot_inspection_service.review_item(
            db, lot_id=lot.id, inspection_id=inspection.id, record_id=item.record_id, tenant_id=seed["tenant"].id,
            field_results=[
                FieldResultIn(field_key="surname", status="defective", error_type="typo"),
                FieldResultIn(field_key="dob", status="defective", error_type="missing"),
            ],
        )
        rows = {r.field_key: r for r in (await db.execute(
            select(LotInspectionFieldResult).where(LotInspectionFieldResult.item_id == item.id)
        )).scalars()}
        assert (rows["surname"].error_type, rows["surname"].severity) == (ErrorType.typo, Severity.minor)
        assert (rows["dob"].error_type, rows["dob"].severity) == (ErrorType.missing, Severity.critical)

    async def test_customer_qc_uses_the_projects_agreement(self, db: AsyncSession, seed):
        seed["aql_config"].error_classes = {"missing": "major", "typo": "major", "interpretation": "minor"}
        task, record, lot = await _make_qc_setup(db, seed, acceptance_number=5)
        await task_service.fail_task(
            db, task_id=task.id, user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id, reason="typo",
            field_results=[
                FieldResultIn(field_key="surname", status="defective", error_type="typo"),
                FieldResultIn(field_key="dob", status="accepted"),
            ],
        )
        rows = {r.field_key: r for r in (await db.execute(
            select(QcFieldResult).where(QcFieldResult.task_id == task.id)
        )).scalars()}
        assert (rows["surname"].error_type, rows["surname"].severity) == (ErrorType.typo, Severity.major)
        assert (rows["dob"].error_type, rows["dob"].severity) == (None, None)


class TestOutgoingInspectionVerdict:
    """20 records -> C. Major 1.0 needs E (n=13); minor 4.0 at E is Ac 1;
    critical is zero tolerance. So: n=13, Ac critical 0 / major 0 / minor 1."""

    async def _inspect(self, db, seed, defects: dict[int, dict]):
        from tests.test_lot_inspections import _complete, _review_all
        cfg = seed["aql_config"]
        cfg.critical_aql, cfg.normal_aql, cfg.minor_aql = None, 1.0, 4.0
        await db.flush()
        lot, _ = await _make_lot(db, seed, num_records=20)
        inspection = await _start(db, seed, lot)
        assert inspection.sample_size == 13
        assert {c: p["acceptance_number"] for c, p in inspection.class_plan.items()} == {
            "critical": 0, "major": 0, "minor": 1,
        }
        await _review_all(db, seed, lot, inspection, defects)
        await _complete(db, seed, lot, inspection)
        return lot, inspection

    async def test_one_typo_is_within_minor_ac(self, db: AsyncSession, seed):
        _, insp = await self._inspect(db, seed, {0: {"surname": ("", "typo")}})
        assert insp.status.value == "passed"
        assert (insp.critical_defect_count, insp.major_defect_count, insp.minor_defect_count) == (0, 0, 1)

    async def test_typos_add_up_across_the_sample(self, db: AsyncSession, seed):
        _, insp = await self._inspect(db, seed, {0: {"surname": ("", "typo")}, 3: {"surname": ("", "interpretation")}})
        assert insp.status.value == "failed" and insp.minor_defect_count == 2

    async def test_one_missing_value_fails_major(self, db: AsyncSession, seed):
        _, insp = await self._inspect(db, seed, {0: {"surname": ("", "missing")}})
        assert insp.status.value == "failed" and insp.major_defect_count == 1

    async def test_identifier_typo_is_critical(self, db: AsyncSession, seed):
        _, insp = await self._inspect(db, seed, {0: {"dob": ("", "typo")}})
        assert insp.status.value == "failed" and insp.critical_defect_count == 1

    async def test_report_shows_classes_and_error_types(self, db: AsyncSession, seed):
        lot, insp = await self._inspect(db, seed, {0: {"surname": ("smudged", "typo")}})
        report = await lot_inspection_service.build_report(db, lot=lot)
        entry = report["inspections"][0]
        assert entry["classes"]["minor"] == {
            "aql": 4.0, "acceptance_number": 1, "rejection_number": 2, "found": 1, "accepted": True,
        }
        assert entry["classes"]["critical"]["aql"] is None
        marks = [f for i in entry["items"] for f in i["field_results"] if f["status"] == "defective"]
        assert [(f["field_key"], f["error_type"], f["severity"], f["note"]) for f in marks] == [
            ("surname", "typo", "minor", "smudged"),
        ]
        assert entry["field_tally"][0] == {
            "field_key": "surname", "title": "Surname", "is_critical": False,
            "defective_count": 1, "critical": 0, "major": 0, "minor": 1,
        }

    async def test_defective_mark_needs_an_error_type(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=5)
        inspection = await _start(db, seed, lot)
        item = (await _items(db, inspection))[0]
        with pytest.raises(HTTPException) as e:
            await lot_inspection_service.review_item(
                db, lot_id=lot.id, inspection_id=inspection.id, record_id=item.record_id,
                tenant_id=seed["tenant"].id,
                field_results=[
                    FieldResultIn(field_key="surname", status="defective"),
                    FieldResultIn(field_key="dob", status="accepted"),
                ],
            )
        assert e.value.status_code == 422 and "surname" in e.value.detail

    async def test_inspection_from_before_classes_keeps_the_old_rule(self, db: AsyncSession, seed):
        from tests.test_lot_inspections import _complete, _review_all
        lot, _ = await _make_lot(db, seed, num_records=20)
        inspection = await _start(db, seed, lot)
        inspection.class_plan = None  # as migrated
        inspection.acceptance_number = 1
        await db.flush()
        await _review_all(db, seed, lot, inspection, {0: {"surname": ("", "typo")}, 1: {"surname": ("", "missing")}})
        await _complete(db, seed, lot, inspection)
        # Two non-critical defects against Ac 1, whatever their type.
        assert inspection.status.value == "failed"
        assert (inspection.critical_defect_count, inspection.major_defect_count, inspection.minor_defect_count) == (0, None, 2)
        report = await lot_inspection_service.build_report(db, lot=lot)
        assert report["inspections"][0]["classes"] is None

    async def test_level_chooser_shows_class_plans(self, db: AsyncSession, seed, client):
        cfg = seed["aql_config"]
        cfg.critical_aql, cfg.normal_aql, cfg.minor_aql = None, 1.0, 4.0
        await db.flush()
        lot, _ = await _make_lot(db, seed, num_records=20)
        await db.commit()
        headers = {"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": "digitizing"}
        body = (await client.get(f"/api/lots/{lot.id}/inspections", headers=headers)).json()
        assert body["class_aqls"] == {"critical": None, "major": 1.0, "minor": 4.0}
        normal = body["level_plans"]["normal"]
        assert normal["sample_size"] == 13 and normal["acceptance_number"] == 0
        assert normal["classes"]["minor"]["acceptance_number"] == 1

    async def test_customer_report_has_no_aqls(self, db: AsyncSession, seed):
        lot, _ = await self._inspect(db, seed, {})
        report = await lot_inspection_service.build_report(db, lot=lot, include_aql=False)
        classes = report["inspections"][0]["classes"]
        assert all("aql" not in c for c in classes.values())
        assert classes["minor"]["acceptance_number"] == 1


class TestCustomerQc:
    """Customer ISO sample of a 20-record lot with critical zero tolerance,
    major 1.0, minor 4.0: n=13, Ac critical 0 / major 0 / minor 1."""

    async def _sample(self, db, seed):
        from tests.test_lot_resubmit import _inspect_and_release
        from app.services import lot_service
        cfg = seed["aql_config"]
        cfg.critical_aql, cfg.normal_aql, cfg.minor_aql = None, 1.0, 4.0
        await db.flush()
        lot, records = await _make_lot(db, seed, num_records=20)
        await _inspect_and_release(db, seed, lot)
        await lot_service.apply_sample(
            db, lot_id=lot.id, sample_rate=None, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        assert lot.sample_size == 13
        assert {c: p["acceptance_number"] for c, p in lot.class_plan.items()} == {"critical": 0, "major": 0, "minor": 1}
        assert lot.acceptance_number == 0  # the major class's
        return lot, records

    async def _review(self, db, seed, lot, defects: dict[int, list[FieldResultIn]]):
        """QC every sampled record: record i gets defects[i] (fail) or all accepted."""
        from datetime import datetime, timezone
        from app.models.batch import Batch
        from app.models.document_type import DocumentType
        from app.models.lot import LotRecord
        from app.models.record import Record
        from app.models.task import Task, TaskStatus, TaskType
        from app.services import lot_service
        doc_type = (await db.execute(
            select(DocumentType).join(Batch, Batch.document_type_id == DocumentType.id)
            .join(Record, Record.batch_id == Batch.id).join(LotRecord, LotRecord.record_id == Record.id)
            .where(LotRecord.lot_id == lot.id).limit(1)
        )).scalar_one()
        sampled = sorted((await db.execute(
            select(LotRecord.record_id).where(LotRecord.lot_id == lot.id, LotRecord.is_sampled == True)  # noqa: E712
        )).scalars().all())
        await lot_service.create_qc_batches(
            db, lot_id=lot.id, project_id=lot.project_id, document_type_id=doc_type.id,
            assignments=[{"agent_id": seed["qc_agent"].id, "record_ids": sampled}],
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        tasks = (await db.execute(
            select(Task).where(Task.record_id.in_(sampled), Task.task_type == TaskType.qc).order_by(Task.record_id)
        )).scalars().all()
        accept = [FieldResultIn(field_key="surname", status="accepted"), FieldResultIn(field_key="dob", status="accepted")]
        for i, task in enumerate(tasks):
            task.status, task.started_at = TaskStatus.in_progress, datetime.now(timezone.utc)
            await db.flush()
            kw = dict(task_id=task.id, user_id=seed["qc_agent"].id, tenant_id=seed["tenant"].id)
            if i in defects:
                await task_service.fail_task(db, reason="defect", field_results=defects[i], **kw)
            else:
                await task_service.complete_task(db, field_results=accept, **kw)
        await db.flush()
        await db.refresh(lot)

    @staticmethod
    def _bad(field, error_type):
        other = "dob" if field == "surname" else "surname"
        return [FieldResultIn(field_key=field, status="defective", error_type=error_type),
                FieldResultIn(field_key=other, status="accepted")]

    async def test_one_typo_passes(self, db: AsyncSession, seed):
        lot, _ = await self._sample(db, seed)
        await self._review(db, seed, lot, {0: self._bad("surname", "typo")})
        assert lot.status.value == "passed"
        assert (lot.critical_defect_count, lot.major_defect_count, lot.minor_defect_count) == (0, 0, 1)

    async def test_typos_add_up(self, db: AsyncSession, seed):
        lot, _ = await self._sample(db, seed)
        await self._review(db, seed, lot, {0: self._bad("surname", "typo"), 5: self._bad("surname", "interpretation")})
        assert lot.status.value == "failed" and lot.minor_defect_count == 2

    async def test_missing_fails_major(self, db: AsyncSession, seed):
        lot, _ = await self._sample(db, seed)
        await self._review(db, seed, lot, {0: self._bad("surname", "missing")})
        assert lot.status.value == "failed" and lot.major_defect_count == 1

    async def test_identifier_error_is_critical(self, db: AsyncSession, seed):
        lot, _ = await self._sample(db, seed)
        await self._review(db, seed, lot, {0: self._bad("dob", "typo")})
        assert lot.status.value == "failed" and lot.critical_defect_count == 1

    async def test_tabulation(self, db: AsyncSession, seed):
        from app.services import lot_service
        lot, _ = await self._sample(db, seed)
        await self._review(db, seed, lot, {0: self._bad("surname", "typo")})
        tab = await lot_service.get_field_result_tabulation(db, lot_id=lot.id, tenant_id=seed["tenant"].id)
        assert tab["classes"]["minor"] == {
            "aql": 4.0, "acceptance_number": 1, "rejection_number": 2, "found": 1, "accepted": True,
        }
        surname = next(f for f in tab["fields"] if f["field_key"] == "surname")
        assert surname["by_class"] == {"critical": 0, "major": 0, "minor": 1}
        assert surname["by_error_type"] == {"typo": 1}

    async def test_defect_needs_error_type(self, db: AsyncSession, seed):
        lot, _ = await self._sample(db, seed)
        with pytest.raises(HTTPException) as e:
            await self._review(db, seed, lot, {0: [
                FieldResultIn(field_key="surname", status="defective"), FieldResultIn(field_key="dob", status="accepted"),
            ]})
        assert e.value.status_code == 422

    async def test_resubmit_archives_the_class_plan(self, db: AsyncSession, seed):
        from app.models.lot import LotQcRound
        from app.services import lot_service
        from tests.test_lot_resubmit import _finish_rework
        lot, records = await self._sample(db, seed)
        await self._review(db, seed, lot, {0: self._bad("surname", "missing")})
        plan = lot.class_plan
        await lot_service.send_for_remediation(db, lot_id=lot.id, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id)
        await _finish_rework(db, records)
        await lot_service.resubmit_lot(db, lot_id=lot.id, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id)
        archived = (await db.execute(select(LotQcRound).where(LotQcRound.lot_id == lot.id))).scalar_one()
        assert archived.class_plan == plan and archived.major_defect_count == 1
        assert lot.class_plan is None and lot.major_defect_count is None
