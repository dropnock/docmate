"""Error rate must be tracked per agent (staff_productivity) and per project
(project_kpis, from AQL QC results)."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskStatus, TaskType
from app.services import analytics_service, aql_service, task_service


class TestAgentErrorRate:
    async def test_failed_task_counts_toward_error_rate(self, db: AsyncSession, seed):
        # qa_staff completes one QA task successfully...
        ok_task = await task_service.assign_task(
            db, record_id=seed["record"].id, batch_id=seed["batch"].id,
            task_type=TaskType.qa, agent_id=seed["qa_staff"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(db, task_id=ok_task.id, user_id=seed["qa_staff"].id, tenant_id=seed["tenant"].id)
        await task_service.complete_task(db, task_id=ok_task.id, user_id=seed["qa_staff"].id, tenant_id=seed["tenant"].id)

        # ...and fails a second one.
        bad_task = await task_service.assign_task(
            db, record_id=seed["record2"].id, batch_id=seed["batch"].id,
            task_type=TaskType.qa, agent_id=seed["qa_staff"].id,
            supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        await task_service.start_task(db, task_id=bad_task.id, user_id=seed["qa_staff"].id, tenant_id=seed["tenant"].id)
        await task_service.fail_task(
            db, task_id=bad_task.id, user_id=seed["qa_staff"].id,
            reason="Wrong field mapping", tenant_id=seed["tenant"].id,
        )
        await db.flush()

        failed = await db.get(Task, bad_task.id)
        assert failed.status == TaskStatus.failed

        rows = await analytics_service.staff_productivity(db, project_id=seed["project"].id)
        qa_metrics = next(r for r in rows if r["user_id"] == seed["qa_staff"].id)["qa"]
        assert qa_metrics["total_records_processed"] == 1
        assert qa_metrics["error_rate"] == 0.5


class TestProjectErrorRate:
    async def test_project_kpis_reports_defect_rate_from_qc_results(self, db: AsyncSession, seed):
        # 20-record batch -> code letter D -> sample 8, accept 0 at AQL 1.5
        await aql_service.evaluate_batch(
            db,
            project_id=seed["project"].id,
            batch_id=seed["batch"].id,
            batch_size=20,
            defects_found=2,
            tenant_id=seed["tenant"].id,
            performed_by=seed["qc_agent"].id,
        )
        await db.flush()

        kpis = await analytics_service.project_kpis(db, project_id=seed["project"].id)
        assert kpis["records_inspected"] == 8
        assert kpis["defects_found"] == 2
        assert kpis["error_rate"] == 0.25

    async def test_project_kpis_error_rate_zero_with_no_qc_results(self, db: AsyncSession, seed):
        kpis = await analytics_service.project_kpis(db, project_id=seed["project"].id)
        assert kpis["records_inspected"] == 0
        assert kpis["defects_found"] == 0
        assert kpis["error_rate"] == 0.0
