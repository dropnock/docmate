"""Irreversible production cleanup script (scripts/wipe_project_data.py).

Tests exercise the internal async helpers directly against the test DB
fixture, the same way test_backfill_tiff_to_pdf.py does — main() opens its
own session via app.core.database.AsyncSessionLocal, which points at
whatever the app is configured against, not the per-test SQLite DB."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.batch import Batch, BatchQCResult, BatchStatus
from app.models.cabinet import Cabinet
from app.models.lot import Lot, LotRecord, LotStatus
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.models.record_version import RecordVersion, VersionReason
from app.models.task import Task, TaskStatus, TaskType
from scripts import wipe_project_data


async def _build_world(db: AsyncSession, seed):
    """Builds one extra project's worth of data on top of `seed` (which
    already gives us project/batch/record/record2) — a cabinet with a
    converted-TIFF record and a not-yet-converted TIFF record, a version,
    a task, a lot + lot_record, a batch QC result, and one audit_log per
    entity type plus one deliberately unrelated (project-type) audit_log
    that must survive the wipe untouched."""
    project = seed["project"]
    project.s3_bucket_name = "test-bucket"

    cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=project.id, name="Wipe Cabinet")
    db.add(cabinet)
    await db.flush()

    converted = Record(
        cabinet_id=cabinet.id,
        status=RecordStatus.indexed,
        current_version=1,
        file_reference=f"cabinets/{cabinet.id}/scan-a.pdf",
        source_identifier="scan-a",
    )
    unconverted_tiff = Record(
        cabinet_id=cabinet.id,
        status=RecordStatus.pending,
        current_version=1,
        file_reference=f"cabinets/{cabinet.id}/scan-b.tiff",
        source_identifier="scan-b",
    )
    db.add_all([converted, unconverted_tiff])
    await db.flush()

    version = RecordVersion(
        record_id=converted.id, version_number=1, indexed_data={"x": 1},
        created_by=seed["indexer"].id, reason=VersionReason.initial_indexing,
    )
    db.add(version)

    task = Task(
        record_id=converted.id, batch_id=seed["batch"].id,
        task_type=TaskType.indexing, status=TaskStatus.completed,
    )
    db.add(task)
    await db.flush()

    lot = Lot(tenant_id=seed["tenant"].id, project_id=project.id, name="Lot 1", status=LotStatus.draft)
    db.add(lot)
    await db.flush()
    lot_record = LotRecord(lot_id=lot.id, record_id=converted.id, is_sampled=True)
    db.add(lot_record)

    qc_result = BatchQCResult(
        batch_id=seed["batch"].id, total_inspected=2, defects_found=0,
        acceptance_number=1, aql_level_applied=1.5, outcome="passed",
    )
    db.add(qc_result)

    record_audit = AuditLog(
        tenant_id=seed["tenant"].id, entity_type=AuditEntityType.record, entity_id=converted.id,
        action=AuditAction.created, performed_by=seed["admin"].id,
    )
    task_audit = AuditLog(
        tenant_id=seed["tenant"].id, entity_type=AuditEntityType.task, entity_id=task.id,
        action=AuditAction.assigned, performed_by=seed["admin"].id,
    )
    batch_audit = AuditLog(
        tenant_id=seed["tenant"].id, entity_type=AuditEntityType.batch, entity_id=seed["batch"].id,
        action=AuditAction.created, performed_by=seed["admin"].id,
    )
    unrelated_audit = AuditLog(
        tenant_id=seed["tenant"].id, entity_type=AuditEntityType.project, entity_id=project.id,
        action=AuditAction.created, performed_by=seed["admin"].id,
    )
    db.add_all([record_audit, task_audit, batch_audit, unrelated_audit])
    await db.commit()

    return {
        "cabinet": cabinet, "converted": converted, "unconverted_tiff": unconverted_tiff,
        "version": version, "task": task, "lot": lot, "lot_record": lot_record,
        "qc_result": qc_result, "unrelated_audit_id": unrelated_audit.id,
    }


class TestGatherScope:
    async def test_counts_and_s3_keys(self, db: AsyncSession, seed):
        world = await _build_world(db, seed)
        scope = await wipe_project_data._gather_scope(db, seed["project"])

        # seed() already put record/record2 on seed["batch"] with no
        # cabinet_id — those are in scope via batch_id, alongside our two
        # cabinet-linked records.
        assert set(scope["record_ids"]) == {
            seed["record"].id, seed["record2"].id, world["converted"].id, world["unconverted_tiff"].id,
        }
        assert scope["task_ids"] == [world["task"].id]
        assert scope["lot_ids"] == [world["lot"].id]
        assert scope["lot_record_count"] == 1
        assert scope["batch_ids"] == [seed["batch"].id]
        assert scope["batch_qc_result_count"] == 1
        assert scope["record_version_count"] == 1
        assert scope["audit_log_count"] == 3  # record + task + batch, not the unrelated project one
        assert scope["cabinet_ids"] == [world["cabinet"].id]
        assert scope["s3_keys"] == sorted([
            f"cabinets/{world['cabinet'].id}/scan-a.pdf",
            f"cabinets/{world['cabinet'].id}/scan-a.tif",
            f"cabinets/{world['cabinet'].id}/scan-a.tiff",
            f"cabinets/{world['cabinet'].id}/scan-b.tiff",
        ])

    async def test_project_with_nothing_to_delete(self, db: AsyncSession, seed):
        other = Project(
            tenant_id=seed["tenant"].id, digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id, name="Empty Project",
        )
        db.add(other)
        await db.commit()

        scope = await wipe_project_data._gather_scope(db, other)
        assert scope["record_ids"] == []
        assert scope["cabinet_ids"] == []
        assert scope["s3_keys"] == []


class TestDeleteDbRows:
    async def test_deletes_everything_but_keeps_project_and_cabinet(self, db: AsyncSession, seed):
        world = await _build_world(db, seed)
        project = seed["project"]
        scope = await wipe_project_data._gather_scope(db, project)

        await wipe_project_data._delete_db_rows(db, scope)
        await db.commit()

        assert await db.get(Project, project.id) is not None
        assert await db.get(Cabinet, world["cabinet"].id) is not None

        assert await db.get(Record, seed["record"].id) is None
        assert await db.get(Record, seed["record2"].id) is None
        assert await db.get(Record, world["converted"].id) is None
        assert await db.get(Record, world["unconverted_tiff"].id) is None
        assert await db.get(RecordVersion, world["version"].id) is None
        assert await db.get(Task, world["task"].id) is None
        assert await db.get(Lot, world["lot"].id) is None
        assert await db.get(LotRecord, world["lot_record"].id) is None
        assert await db.get(BatchQCResult, world["qc_result"].id) is None
        assert await db.get(Batch, seed["batch"].id) is None

        # The project-type audit_log is unrelated to any deleted entity —
        # must survive.
        assert await db.get(AuditLog, world["unrelated_audit_id"]) is not None


class TestDeleteS3Objects:
    async def test_best_effort_continues_past_one_failure(self, db: AsyncSession, seed, monkeypatch):
        world = await _build_world(db, seed)
        project = seed["project"]
        scope = await wipe_project_data._gather_scope(db, project)

        calls = []

        async def fake_delete_object(bucket, key):
            calls.append((bucket, key))
            if key.endswith("scan-a.tif"):
                raise RuntimeError("boom")

        monkeypatch.setattr(wipe_project_data.s3_service, "delete_object", fake_delete_object)

        failures = await wipe_project_data._delete_s3_objects(project, scope)

        assert len(calls) == len(scope["s3_keys"])  # kept going after the failure
        assert failures == [(f"cabinets/{world['cabinet'].id}/scan-a.tif", "boom")]

    async def test_no_bucket_configured_skips_entirely(self, db: AsyncSession, seed, monkeypatch):
        project = seed["project"]
        project.s3_bucket_name = None
        scope = {"s3_keys": ["some/key.pdf"]}

        called = False

        async def fake_delete_object(bucket, key):
            nonlocal called
            called = True

        monkeypatch.setattr(wipe_project_data.s3_service, "delete_object", fake_delete_object)

        failures = await wipe_project_data._delete_s3_objects(project, scope)
        assert failures == []
        assert called is False


class TestResolveProject:
    async def test_by_id(self, db: AsyncSession, seed):
        project = await wipe_project_data._resolve_project(
            db, project_id=seed["project"].id, project_name=None, tenant_id=None
        )
        assert project.id == seed["project"].id

    async def test_by_unique_name(self, db: AsyncSession, seed):
        project = await wipe_project_data._resolve_project(
            db, project_id=None, project_name=seed["project"].name, tenant_id=None
        )
        assert project.id == seed["project"].id

    async def test_unknown_name_exits(self, db: AsyncSession, seed):
        with pytest.raises(SystemExit):
            await wipe_project_data._resolve_project(
                db, project_id=None, project_name="does-not-exist", tenant_id=None
            )

    async def test_ambiguous_name_across_tenants_exits(self, db: AsyncSession, seed):
        from app.models.tenant import Tenant

        other_tenant = Tenant(name="Other Corp", slug="othercorp")
        db.add(other_tenant)
        await db.flush()

        dupe = Project(
            tenant_id=other_tenant.id, digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id, name=seed["project"].name,
        )
        db.add(dupe)
        await db.commit()

        with pytest.raises(SystemExit):
            await wipe_project_data._resolve_project(
                db, project_id=None, project_name=seed["project"].name, tenant_id=None
            )

        # Disambiguating with tenant_id resolves it.
        project = await wipe_project_data._resolve_project(
            db, project_id=None, project_name=seed["project"].name, tenant_id=seed["tenant"].id
        )
        assert project.id == seed["project"].id


class TestParseArgs:
    def test_requires_project_id_or_name(self):
        with pytest.raises(SystemExit):
            wipe_project_data.parse_args([])

    def test_rejects_both_id_and_name(self):
        with pytest.raises(SystemExit):
            wipe_project_data.parse_args(["--project-id", "1", "--project-name", "x"])

    def test_dry_run_is_the_default(self):
        args = wipe_project_data.parse_args(["--project-id", "1"])
        assert args.confirm is False


class TestWriteReport:
    async def test_report_contents(self, db: AsyncSession, seed, tmp_path):
        world = await _build_world(db, seed)
        project = seed["project"]
        scope = await wipe_project_data._gather_scope(db, project)

        path = tmp_path / "report.json"
        wipe_project_data._write_report(
            path, project, scope, dry_run=False, s3_failures=[("bad/key.pdf", "boom")]
        )

        import json
        report = json.loads(path.read_text())
        assert report["dry_run"] is False
        assert report["project"]["id"] == project.id
        assert report["cabinets_kept"] == [world["cabinet"].id]
        assert set(report["deleted"]["record_ids"]) == set(scope["record_ids"])
        assert report["s3"]["delete_failures"] == [{"key": "bad/key.pdf", "error": "boom"}]
