"""scripts/refresh_training_project.py — clean out a training project and
copy qa_passed records (data + image) into it. The helpers are exercised
against the test DB with S3 faked in memory, the same way
test_wipe_project_data.py does; main() itself opens the app's own session."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet import Cabinet
from app.models.project import Project
from app.models.record import Record, RecordStatus
from scripts import refresh_training_project as refresh
from scripts import wipe_project_data as wipe


async def _world(db: AsyncSession, seed):
    source = seed["project"]
    source.s3_bucket_name = "src-bucket"
    training = Project(
        tenant_id=seed["tenant"].id, digitizing_org_id=source.digitizing_org_id,
        customer_org_id=source.customer_org_id, name="Training", s3_bucket_name="train-bucket",
    )
    db.add(training)
    await db.flush()
    src_cab = Cabinet(tenant_id=seed["tenant"].id, project_id=source.id, name="Source")
    train_cab = Cabinet(tenant_id=seed["tenant"].id, project_id=training.id, name="Train")
    db.add_all([src_cab, train_cab])
    await db.flush()
    passed = [
        Record(cabinet_id=src_cab.id, status=RecordStatus.qa_passed, current_version=3,
               file_reference=f"projects/{source.id}/cabinets/{src_cab.id}/u{i}/card{i}.png",
               original_filename=f"card{i}.png", source_identifier=f"card{i}", indexed_data={"n": i})
        for i in range(3)
    ]
    other = Record(cabinet_id=src_cab.id, status=RecordStatus.indexed, current_version=2,
                   file_reference="x/not-passed.png")
    old = Record(cabinet_id=train_cab.id, status=RecordStatus.pending, current_version=1,
                 file_reference=f"projects/{training.id}/cabinets/{train_cab.id}/old/old.png")
    db.add_all([*passed, other, old])
    await db.commit()
    return {"source": source, "training": training, "train_cab": train_cab, "passed": passed, "old": old}


class _FakeS3:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    def install(self, monkeypatch):
        s3 = refresh.s3_service

        async def get_bytes(bucket, key):
            return self.objects.get((bucket, key), b"img:" + key.encode())

        async def content_type(bucket, key):
            return "image/png"

        async def put_bytes(bucket, key, data, ctype):
            self.objects[(bucket, key)] = data

        async def delete(bucket, key):
            self.deleted.append((bucket, key))

        monkeypatch.setattr(s3, "get_object_bytes", get_bytes)
        monkeypatch.setattr(s3, "resolve_content_type", content_type)
        monkeypatch.setattr(s3, "put_object_bytes", put_bytes)
        monkeypatch.setattr(s3, "delete_object", delete)


class TestRefresh:
    async def test_wipe_then_copy_qa_passed_into_training_cabinet(self, db: AsyncSession, seed, monkeypatch):
        w = await _world(db, seed)
        fake = _FakeS3()
        fake.install(monkeypatch)

        assert await refresh._count_qa_passed(db, w["source"]) == 3

        scope = await wipe._gather_scope(db, w["training"])
        assert scope["record_ids"] == [w["old"].id]
        await wipe._delete_db_rows(db, scope)
        await db.commit()
        await wipe._delete_s3_objects(w["training"], scope)
        assert ("train-bucket", w["old"].file_reference) in fake.deleted

        records = await refresh._select_source_records(db, w["source"], limit=2, order="newest")
        assert [r.id for r in records] == [w["passed"][2].id, w["passed"][1].id]
        copied, failures = await refresh._copy_records(
            db, records, target_project=w["training"], cabinet=w["train_cab"],
        )
        assert failures == [] and len(copied) == 2

        rows = list((await db.execute(
            select(Record).where(Record.cabinet_id == w["train_cab"].id).order_by(Record.id)
        )).scalars().all())
        assert len(rows) == 2
        for row, src in zip(rows, [w["passed"][2], w["passed"][1]]):
            assert row.status == RecordStatus.qa_passed and row.batch_id is None
            assert row.indexed_data == src.indexed_data and row.source_identifier == src.source_identifier
            assert row.current_version == 1
            assert row.file_reference.startswith(f"projects/{w['training'].id}/cabinets/{w['train_cab'].id}/")
            assert fake.objects[("train-bucket", row.file_reference)] == b"img:" + src.file_reference.encode()
        # Source records are untouched.
        assert (await db.get(Record, w["passed"][0].id)).status == RecordStatus.qa_passed

    async def test_copy_failure_is_reported_and_others_continue(self, db: AsyncSession, seed, monkeypatch):
        w = await _world(db, seed)
        fake = _FakeS3()
        fake.install(monkeypatch)
        real_get = refresh.s3_service.get_object_bytes

        async def flaky(bucket, key):
            if key.endswith("card1.png"):
                raise RuntimeError("boom")
            return await real_get(bucket, key)

        monkeypatch.setattr(refresh.s3_service, "get_object_bytes", flaky)
        records = await refresh._select_source_records(db, w["source"], limit=10, order="oldest")
        copied, failures = await refresh._copy_records(
            db, records, target_project=w["training"], cabinet=w["train_cab"],
        )
        assert len(copied) == 2
        assert failures == [{"source_record_id": w["passed"][1].id, "error": "boom"}]

    def test_count_prompt_defaults_to_1000(self, monkeypatch):
        answers = iter(["", ])
        monkeypatch.setattr("builtins.input", lambda _p: next(answers))
        assert refresh._prompt_count(1000) == 1000
        answers2 = iter(["abc", "0", "250"])
        monkeypatch.setattr("builtins.input", lambda _p: next(answers2))
        assert refresh._prompt_count(1000) == 250
