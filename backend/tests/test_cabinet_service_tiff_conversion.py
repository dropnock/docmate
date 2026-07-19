"""Upload-time TIFF-to-PDF conversion hook (cabinet_service._convert_tiff_if_needed),
wired into ingest_image_create_or_link so a scan never needs re-decoding on
every future view — see batches.py's get_record_image self-heal fallback,
which does the same conversion for anything this hook missed."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet import Cabinet
from app.models.record import Record
from app.services import cabinet_service, image_service, s3_service


async def _make_cabinet(db: AsyncSession, seed) -> Cabinet:
    cabinet = Cabinet(
        tenant_id=seed["tenant"].id,
        project_id=seed["project"].id,
        name="Test Cabinet",
    )
    db.add(cabinet)
    await db.flush()
    return cabinet


class TestUploadTimeTiffConversion:
    async def test_tiff_upload_is_converted_to_pdf(self, db: AsyncSession, seed, monkeypatch):
        seed["project"].s3_bucket_name = "test-bucket"
        await db.flush()
        cabinet = await _make_cabinet(db, seed)

        put_calls = []

        async def fake_content_type(bucket, key):
            return "image/tiff"

        async def fake_get_object_bytes(bucket, key):
            return b"fake-tiff-bytes"

        def fake_tiff_to_pdf(data):
            return b"fake-pdf-bytes"

        async def fake_put_object_bytes(bucket, key, data, content_type):
            put_calls.append((bucket, key, data, content_type))

        monkeypatch.setattr(s3_service, "get_object_content_type", fake_content_type)
        monkeypatch.setattr(s3_service, "get_object_bytes", fake_get_object_bytes)
        monkeypatch.setattr(image_service, "tiff_to_pdf", fake_tiff_to_pdf)
        monkeypatch.setattr(s3_service, "put_object_bytes", fake_put_object_bytes)

        record = await cabinet_service.ingest_image_create_or_link(
            db,
            cabinet_id=cabinet.id,
            original_filename="scan007.tiff",
            s3_key=f"cabinets/{cabinet.id}/scan007.tiff",
            tenant_id=seed["tenant"].id,
            user_id=seed["admin"].id,
        )

        assert record.file_reference == f"cabinets/{cabinet.id}/scan007.pdf"
        assert put_calls == [
            ("test-bucket", f"cabinets/{cabinet.id}/scan007.pdf", b"fake-pdf-bytes", "application/pdf")
        ]

    async def test_non_tiff_upload_is_untouched(self, db: AsyncSession, seed, monkeypatch):
        seed["project"].s3_bucket_name = "test-bucket"
        await db.flush()
        cabinet = await _make_cabinet(db, seed)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("conversion should not run for a non-TIFF upload")

        async def fake_content_type(bucket, key):
            return "image/png"

        monkeypatch.setattr(s3_service, "get_object_content_type", fake_content_type)
        monkeypatch.setattr(s3_service, "get_object_bytes", fail_if_called)
        monkeypatch.setattr(image_service, "tiff_to_pdf", fail_if_called)
        monkeypatch.setattr(s3_service, "put_object_bytes", fail_if_called)

        record = await cabinet_service.ingest_image_create_or_link(
            db,
            cabinet_id=cabinet.id,
            original_filename="scan008.png",
            s3_key=f"cabinets/{cabinet.id}/scan008.png",
            tenant_id=seed["tenant"].id,
            user_id=seed["admin"].id,
        )

        assert record.file_reference == f"cabinets/{cabinet.id}/scan008.png"

    async def test_existing_record_relink_also_converts(self, db: AsyncSession, seed, monkeypatch):
        seed["project"].s3_bucket_name = "test-bucket"
        await db.flush()
        cabinet = await _make_cabinet(db, seed)

        existing = Record(cabinet_id=cabinet.id, source_identifier="scan009", status=seed["record"].status)
        db.add(existing)
        await db.flush()

        async def fake_content_type(bucket, key):
            return "image/tiff"

        async def fake_get_object_bytes(bucket, key):
            return b"fake-tiff-bytes"

        def fake_tiff_to_pdf(data):
            return b"fake-pdf-bytes"

        async def fake_put_object_bytes(bucket, key, data, content_type):
            pass

        monkeypatch.setattr(s3_service, "get_object_content_type", fake_content_type)
        monkeypatch.setattr(s3_service, "get_object_bytes", fake_get_object_bytes)
        monkeypatch.setattr(image_service, "tiff_to_pdf", fake_tiff_to_pdf)
        monkeypatch.setattr(s3_service, "put_object_bytes", fake_put_object_bytes)

        record = await cabinet_service.ingest_image_create_or_link(
            db,
            cabinet_id=cabinet.id,
            original_filename="scan009.tiff",
            s3_key=f"cabinets/{cabinet.id}/scan009.tiff",
            tenant_id=seed["tenant"].id,
            user_id=seed["admin"].id,
        )

        assert record.id == existing.id
        assert record.file_reference == f"cabinets/{cabinet.id}/scan009.pdf"
