"""Same-origin record image proxy endpoint (GET /records/{id}/image).

Proxies bytes through the backend instead of redirecting the browser to a
presigned S3/MinIO URL on a separate origin — see s3_service.stream_object
for why (background image/tile fetches to an untrusted second TLS origin
fail with no user-actionable prompt, unlike top-level navigation)."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import image_service, s3_service
from tests.conftest import token


async def _fake_stream(bucket: str, key: str):
    yield b"first-chunk-"
    yield b"second-chunk"


async def _fake_content_type(bucket: str, key: str) -> str:
    return "image/png"


async def _fake_generic_content_type(bucket: str, key: str) -> str:
    return "application/octet-stream"


async def _fake_sniff_png(bucket: str, key: str) -> str:
    return "image/png"


class TestRecordImageProxy:
    async def test_streams_record_image_same_origin(self, db: AsyncSession, seed, client, monkeypatch):
        record = seed["record"]
        record.file_reference = "cabinets/1/doc.png"
        seed["project"].s3_bucket_name = "test-bucket"
        await db.commit()

        monkeypatch.setattr(s3_service, "get_object_content_type", _fake_content_type)
        monkeypatch.setattr(s3_service, "stream_object", _fake_stream)

        resp = await client.get(
            f"/api/records/{record.id}/image",
            headers={"Authorization": f"Bearer {token(seed['indexer'])}"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content == b"first-chunk-second-chunk"

    async def test_falls_back_to_sniffing_when_content_type_generic(self, db: AsyncSession, seed, client, monkeypatch):
        record = seed["record"]
        record.file_reference = "cabinets/1/doc.png"
        seed["project"].s3_bucket_name = "test-bucket"
        await db.commit()

        monkeypatch.setattr(s3_service, "get_object_content_type", _fake_generic_content_type)
        monkeypatch.setattr(s3_service, "sniff_content_type", _fake_sniff_png)
        monkeypatch.setattr(s3_service, "stream_object", _fake_stream)

        resp = await client.get(
            f"/api/records/{record.id}/image",
            headers={"Authorization": f"Bearer {token(seed['indexer'])}"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"

    async def test_404_when_record_has_no_file(self, db: AsyncSession, seed, client):
        record = seed["record"]  # file_reference left unset
        resp = await client.get(
            f"/api/records/{record.id}/image",
            headers={"Authorization": f"Bearer {token(seed['indexer'])}"},
        )
        assert resp.status_code == 404

    async def test_404_for_nonexistent_record(self, db: AsyncSession, seed, client):
        resp = await client.get(
            "/api/records/999999/image",
            headers={"Authorization": f"Bearer {token(seed['indexer'])}"},
        )
        assert resp.status_code == 404


class TestTiffToPdfSelfHeal:
    """A record still pointing at a TIFF (pre-existing data, or a failed
    upload-time conversion) is converted to PDF and repointed on first view,
    so it never falls back to the old per-view PNG decode."""

    async def test_converts_tiff_to_pdf_and_persists_file_reference(
        self, db: AsyncSession, seed, client, monkeypatch
    ):
        record = seed["record"]
        record.file_reference = "cabinets/1/doc.tiff"
        seed["project"].s3_bucket_name = "test-bucket"
        await db.commit()

        put_calls = []

        async def fake_content_type(bucket, key):
            return "image/tiff"

        async def fake_get_object_bytes(bucket, key):
            assert key == "cabinets/1/doc.tiff"
            return b"fake-tiff-bytes"

        def fake_tiff_to_pdf(data):
            assert data == b"fake-tiff-bytes"
            return b"fake-pdf-bytes"

        async def fake_put_object_bytes(bucket, key, data, content_type):
            put_calls.append((bucket, key, data, content_type))

        async def fake_stream(bucket, key):
            assert key == "cabinets/1/doc.pdf"
            yield b"fake-pdf-bytes"

        monkeypatch.setattr(s3_service, "get_object_content_type", fake_content_type)
        monkeypatch.setattr(s3_service, "get_object_bytes", fake_get_object_bytes)
        monkeypatch.setattr(image_service, "tiff_to_pdf", fake_tiff_to_pdf)
        monkeypatch.setattr(s3_service, "put_object_bytes", fake_put_object_bytes)
        monkeypatch.setattr(s3_service, "stream_object", fake_stream)

        resp = await client.get(
            f"/api/records/{record.id}/image",
            headers={"Authorization": f"Bearer {token(seed['indexer'])}"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content == b"fake-pdf-bytes"
        assert put_calls == [("test-bucket", "cabinets/1/doc.pdf", b"fake-pdf-bytes", "application/pdf")]

        await db.refresh(record)
        assert record.file_reference == "cabinets/1/doc.pdf"

    async def test_already_converted_records_are_pure_passthrough(
        self, db: AsyncSession, seed, client, monkeypatch
    ):
        record = seed["record"]
        record.file_reference = "cabinets/1/doc.pdf"
        seed["project"].s3_bucket_name = "test-bucket"
        await db.commit()

        def fail_if_called(*args, **kwargs):
            raise AssertionError("conversion should not run for an already-converted record")

        async def fake_content_type(bucket, key):
            return "application/pdf"

        async def fake_stream(bucket, key):
            yield b"already-pdf-bytes"

        monkeypatch.setattr(s3_service, "get_object_content_type", fake_content_type)
        monkeypatch.setattr(s3_service, "get_object_bytes", fail_if_called)
        monkeypatch.setattr(image_service, "tiff_to_pdf", fail_if_called)
        monkeypatch.setattr(s3_service, "put_object_bytes", fail_if_called)
        monkeypatch.setattr(s3_service, "stream_object", fake_stream)

        resp = await client.get(
            f"/api/records/{record.id}/image",
            headers={"Authorization": f"Bearer {token(seed['indexer'])}"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content == b"already-pdf-bytes"
