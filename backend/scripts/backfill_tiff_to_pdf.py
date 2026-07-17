"""
One-time backfill: converts existing TIFF-referenced records to the derived
multi-page PDF that new uploads get at upload time (see
app.services.cabinet_service._convert_tiff_if_needed and
app.routers.batches.get_record_image's self-heal fallback, which do the same
conversion for records this script hasn't reached yet).

Idempotent and resumable: a record is only selected while its file_reference
still ends in .tif/.tiff, so once it's repointed at the derived .pdf key (by
this script, the upload hook, or a live self-heal) it's permanently excluded
from future runs — the DB itself is the checkpoint, no separate progress
tracking needed. Safe to run concurrently with live traffic or re-run after
a crash.

Run inside the backend container/environment (needs direct DB + S3 access):
    docker compose exec backend python -m scripts.backfill_tiff_to_pdf
"""
import asyncio
import logging

from sqlalchemy import or_, select

from app.core.database import AsyncSessionLocal
from app.models.record import Record
from app.services import image_service, s3_service

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


async def _backfill_batch(db, batch_size: int = BATCH_SIZE) -> int:
    """Converts up to `batch_size` unconverted TIFF records. Returns how many
    candidates it found (0 means the backlog is drained)."""
    result = await db.execute(
        select(Record)
        .where(
            Record.file_reference.is_not(None),
            or_(
                Record.file_reference.ilike("%.tif"),
                Record.file_reference.ilike("%.tiff"),
            ),
        )
        .limit(batch_size)
    )
    records = list(result.scalars().all())

    for record in records:
        try:
            project = await s3_service.resolve_record_project(record, db)
            if not project or not project.s3_bucket_name:
                logger.warning("record %s has no resolvable bucket, skipping", record.id)
                continue
            bucket = project.s3_bucket_name

            content_type = await s3_service.resolve_content_type(bucket, record.file_reference)
            if content_type != "image/tiff":
                # Already converted by a concurrent self-heal, or the
                # extension didn't match the real content — nothing to do.
                continue

            data = await s3_service.get_object_bytes(bucket, record.file_reference)
            pdf_bytes = await asyncio.to_thread(image_service.tiff_to_pdf, data)
            key = s3_service.derived_pdf_key(record.file_reference)
            await s3_service.put_object_bytes(bucket, key, pdf_bytes, "application/pdf")
            record.file_reference = key
        except Exception:
            logger.exception("backfill failed for record %s", record.id)
            # Leave file_reference untouched — retried on the next run, or
            # self-healed on next view in the meantime.

        # Commit per-record: one failure mid-batch shouldn't roll back
        # progress already made on earlier records in the same batch.
        await db.commit()

    return len(records)


async def main() -> None:
    total = 0
    async with AsyncSessionLocal() as db:
        while True:
            n = await _backfill_batch(db)
            total += n
            if n == 0:
                break
    print(f"backfill complete: {total} record(s) processed")


if __name__ == "__main__":
    asyncio.run(main())
