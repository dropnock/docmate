"""One-time backfill script for pre-existing TIFF records (scripts/backfill_tiff_to_pdf.py).

Idempotency matters here specifically: the script is expected to be safe to
re-run after a crash or once the backlog is drained, with no separate
progress-tracking column — see the module docstring for why the
file_reference extension itself is the checkpoint."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet import Cabinet
from app.models.record import Record
from app.services import image_service, s3_service
from scripts import backfill_tiff_to_pdf


class TestBackfillTiffToPdf:
    async def test_converts_unconverted_tiff_records_and_is_idempotent(
        self, db: AsyncSession, seed, monkeypatch
    ):
        seed["project"].s3_bucket_name = "test-bucket"
        await db.flush()

        cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Backfill Cabinet")
        db.add(cabinet)
        await db.flush()

        record = Record(cabinet_id=cabinet.id, file_reference=f"cabinets/{cabinet.id}/old-scan.tiff")
        db.add(record)
        await db.flush()
        await db.commit()

        async def fake_content_type(bucket, key):
            return "image/tiff"

        async def fake_get_object_bytes(bucket, key):
            return b"fake-tiff-bytes"

        def fake_tiff_to_pdf(data):
            return b"fake-pdf-bytes"

        put_calls = []

        async def fake_put_object_bytes(bucket, key, data, content_type):
            put_calls.append((bucket, key, data, content_type))

        monkeypatch.setattr(s3_service, "get_object_content_type", fake_content_type)
        monkeypatch.setattr(s3_service, "get_object_bytes", fake_get_object_bytes)
        monkeypatch.setattr(image_service, "tiff_to_pdf", fake_tiff_to_pdf)
        monkeypatch.setattr(s3_service, "put_object_bytes", fake_put_object_bytes)

        processed = await backfill_tiff_to_pdf._backfill_batch(db)
        assert processed == 1

        await db.refresh(record)
        assert record.file_reference == f"cabinets/{cabinet.id}/old-scan.pdf"
        assert put_calls == [
            ("test-bucket", f"cabinets/{cabinet.id}/old-scan.pdf", b"fake-pdf-bytes", "application/pdf")
        ]

        # Re-running finds nothing left to convert — the .pdf extension no
        # longer matches the backfill's selection filter.
        second_pass = await backfill_tiff_to_pdf._backfill_batch(db)
        assert second_pass == 0
