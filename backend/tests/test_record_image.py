"""Same-origin record image proxy endpoint (GET /records/{id}/image).

Proxies bytes through the backend instead of redirecting the browser to a
presigned S3/MinIO URL on a separate origin — see s3_service.stream_object
for why (background image/tile fetches to an untrusted second TLS origin
fail with no user-actionable prompt, unlike top-level navigation)."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import s3_service
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
