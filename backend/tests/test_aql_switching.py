"""ISO 2859-1 switching (normal / tightened / reduced) driven by lot QC
verdicts — previously only the unreachable batch path ever moved it."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import AQLConfig, AQLStatus
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.lot import LotStatus
from app.services import aql_service
from tests.test_lot_resubmit import (
    _customer_qc, _failed_and_remediated, _finish_rework, _inspect_and_release, _resubmit,
)
from tests.test_lot_inspections import _make_lot


def _config(status=AQLStatus.normal, *, to_tighten=1, to_reduce=3, passes=0, failures=0):
    return AQLConfig(
        project_id=1, current_status=status, consecutive_passes=passes, consecutive_failures=failures,
        passes_to_reduce=to_reduce, failures_to_tighten=to_tighten,
    )


class TestRules:
    def test_normal_tightens_after_configured_rejections(self):
        c = _config(to_tighten=2)
        assert aql_service.apply_switching(c, rejected=True) == (AQLStatus.normal, AQLStatus.normal)
        assert c.consecutive_failures == 1
        assert aql_service.apply_switching(c, rejected=True) == (AQLStatus.normal, AQLStatus.tightened)
        assert (c.consecutive_passes, c.consecutive_failures) == (0, 0)

    def test_acceptance_breaks_a_rejection_run(self):
        c = _config(to_tighten=2)
        aql_service.apply_switching(c, rejected=True)
        aql_service.apply_switching(c, rejected=False)
        aql_service.apply_switching(c, rejected=True)
        assert c.current_status == AQLStatus.normal

    def test_tightened_relaxes_to_normal_after_run_of_acceptances(self):
        c = _config(AQLStatus.tightened, to_reduce=3)
        for _ in range(2):
            aql_service.apply_switching(c, rejected=False)
        assert c.current_status == AQLStatus.tightened
        aql_service.apply_switching(c, rejected=False)
        assert c.current_status == AQLStatus.normal and c.consecutive_passes == 0

    def test_normal_reduces_after_run_of_acceptances(self):
        c = _config(to_reduce=3)
        for _ in range(3):
            aql_service.apply_switching(c, rejected=False)
        assert c.current_status == AQLStatus.reduced

    def test_reduced_returns_to_normal_on_any_rejection(self):
        c = _config(AQLStatus.reduced, to_tighten=1)
        assert aql_service.apply_switching(c, rejected=True) == (AQLStatus.reduced, AQLStatus.normal)

    def test_tightened_stays_tightened_on_rejection(self):
        c = _config(AQLStatus.tightened)
        aql_service.apply_switching(c, rejected=True)
        assert c.current_status == AQLStatus.tightened and c.consecutive_failures == 1

    def test_reduced_stays_reduced_on_acceptance(self):
        c = _config(AQLStatus.reduced, to_reduce=1)
        aql_service.apply_switching(c, rejected=False)
        assert c.current_status == AQLStatus.reduced


class TestDrivenByLots:
    async def test_failed_lot_tightens_project_and_next_lot_samples_tightened(self, db: AsyncSession, seed):
        seed["aql_config"].failures_to_tighten = 1
        await _failed_and_remediated(db, seed)
        await db.refresh(seed["aql_config"])
        assert seed["aql_config"].current_status == AQLStatus.tightened

        event = (await db.execute(select(AuditLog).where(
            AuditLog.entity_type == AuditEntityType.project, AuditLog.action == AuditAction.batch_escalated,
        ))).scalar_one()
        assert event.new_value == {"aql_status": "tightened"}
        assert event.metadata_["lot_passed"] is False

        # The next lot is sampled under tightened inspection.
        nxt, _ = await _make_lot(db, seed, num_records=2)
        await _inspect_and_release(db, seed, nxt)
        await _customer_qc(db, seed, nxt, fail_first=False)
        assert nxt.aql_status_snapshot == AQLStatus.tightened

    async def test_passed_lots_count_toward_reduced(self, db: AsyncSession, seed):
        seed["aql_config"].passes_to_reduce = 2
        for _ in range(2):
            lot, _ = await _make_lot(db, seed, num_records=2)
            await _inspect_and_release(db, seed, lot)
            await _customer_qc(db, seed, lot, fail_first=False)
            assert lot.status == LotStatus.passed
        await db.refresh(seed["aql_config"])
        assert seed["aql_config"].current_status == AQLStatus.reduced

    async def test_resubmitted_rounds_do_not_count(self, db: AsyncSession, seed):
        seed["aql_config"].failures_to_tighten = 5  # stay normal so counters are visible
        lot, records, _, _ = await _failed_and_remediated(db, seed)
        await db.refresh(seed["aql_config"])
        assert seed["aql_config"].consecutive_failures == 1

        await _finish_rework(db, records)
        await _resubmit(db, seed, lot)
        await _inspect_and_release(db, seed, lot)
        await _customer_qc(db, seed, lot, fail_first=False)
        assert lot.status == LotStatus.passed and lot.qc_round == 2

        await db.refresh(seed["aql_config"])
        # Round 2's pass neither resets the failure run nor counts as a pass.
        assert seed["aql_config"].consecutive_failures == 1
        assert seed["aql_config"].consecutive_passes == 0
