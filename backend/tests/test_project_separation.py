"""Projects sharing an organization's bucket stay apart: uploads get keys
unique to their project, cabinet and upload; a record pointing at another
cabinet's image isn't served; and a batch can't mix projects."""
import re

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cabinet import Cabinet
from app.models.document_type import DocumentType
from app.models.project import Project, S3BucketStatus
from app.models.record import Record, RecordStatus
from app.services import cabinet_service, s3_service
from tests.conftest import token


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {token(user)}", "X-Portal": "digitizing"}


async def _cabinet(db: AsyncSession, seed, project=None) -> Cabinet:
    project = project or seed["project"]
    cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=project.id, name=f"Cabinet {project.name}")
    db.add(cabinet)
    await db.flush()
    return cabinet


async def _other_project(db: AsyncSession, seed) -> Project:
    project = Project(
        tenant_id=seed["tenant"].id, digitizing_org_id=seed["de_org"].id, customer_org_id=seed["cust_org"].id,
        name="Earlier Project", s3_bucket_status=S3BucketStatus.ready, s3_bucket_name="test-bucket",
    )
    db.add(project)
    await db.flush()
    return project


class TestImageKeys:
    def test_key_is_scoped_to_project_cabinet_and_upload(self):
        key = s3_service.record_image_key(7, 3, "card.tiff")
        assert re.fullmatch(r"projects/7/cabinets/3/[0-9a-f]{32}/card\.tiff", key)
        assert s3_service.record_image_key(7, 3, "card.tiff") != key

    def test_key_belongs_to(self):
        assert s3_service.key_belongs_to(s3_service.record_image_key(7, 3, "a.pdf"), 7, 3)
        assert s3_service.key_belongs_to("cabinets/3/a.pdf", 7, 3)  # legacy layout
        assert not s3_service.key_belongs_to(s3_service.record_image_key(8, 3, "a.pdf"), 7, 3)
        assert not s3_service.key_belongs_to("cabinets/30/a.pdf", 7, 3)
        assert not s3_service.key_belongs_to("projects/7/cabinets/30/x/a.pdf", 7, 3)

    async def test_upload_uses_a_fresh_project_key_each_time(self, db: AsyncSession, seed, client, monkeypatch):
        seed["project"].s3_bucket_name = "test-bucket"
        cabinet = await _cabinet(db, seed)
        await db.commit()
        puts = []

        async def fake_put(bucket, key, data, content_type):
            puts.append((bucket, key))

        async def fake_content_type(bucket, key):
            return "image/png"

        monkeypatch.setattr(s3_service, "put_object_bytes", fake_put)
        monkeypatch.setattr(s3_service, "get_object_content_type", fake_content_type)

        for _ in range(2):
            resp = await client.post(
                f"/api/cabinets/{cabinet.id}/upload",
                files={"file": ("card-001.png", b"png-bytes", "image/png")},
                headers=_auth(seed["admin"]),
            )
            assert resp.status_code == 200, resp.text

        (b1, k1), (b2, k2) = puts
        prefix = f"projects/{seed['project'].id}/cabinets/{cabinet.id}/"
        assert b1 == b2 == "test-bucket"
        assert k1.startswith(prefix) and k1.endswith("/card-001.png")
        assert k2.startswith(prefix) and k1 != k2  # a re-upload never lands on an existing object
        record = await db.get(Record, resp.json()["id"])
        await db.refresh(record)
        assert record.file_reference == k2


class TestImageGuard:
    async def _record_with(self, db, seed, key: str) -> Record:
        seed["project"].s3_bucket_name = "test-bucket"
        cabinet = await _cabinet(db, seed)
        record = Record(cabinet_id=cabinet.id, file_reference=key.format(cabinet=cabinet.id),
                        status=RecordStatus.pending, current_version=1)
        db.add(record)
        await db.commit()
        return record

    async def test_image_from_another_cabinet_is_refused(self, db: AsyncSession, seed, client, monkeypatch):
        record = await self._record_with(db, seed, "cabinets/999/someone-elses-card.pdf")

        async def must_not_stream(bucket, key):
            raise AssertionError("served another cabinet's image")
            yield b""

        monkeypatch.setattr(s3_service, "stream_object", must_not_stream)
        resp = await client.get(f"/api/records/{record.id}/image", headers=_auth(seed["admin"]))
        assert resp.status_code == 409

    async def test_own_images_are_served_in_either_layout(self, db: AsyncSession, seed, client, monkeypatch):
        async def fake_content_type(bucket, key):
            return "application/pdf"

        async def fake_stream(bucket, key):
            yield b"pdf"

        monkeypatch.setattr(s3_service, "get_object_content_type", fake_content_type)
        monkeypatch.setattr(s3_service, "stream_object", fake_stream)
        for key in ("cabinets/{cabinet}/card.pdf", f"projects/{seed['project'].id}/cabinets/{{cabinet}}/abc/card.pdf"):
            record = await self._record_with(db, seed, key)
            resp = await client.get(f"/api/records/{record.id}/image", headers=_auth(seed["admin"]))
            assert resp.status_code == 200, (key, resp.text)


class TestBatchStaysInProject:
    async def test_cabinet_from_another_project_is_refused(self, db: AsyncSession, seed):
        other = await _other_project(db, seed)
        cabinet = await _cabinet(db, seed, project=other)
        record = Record(cabinet_id=cabinet.id, status=RecordStatus.pending, current_version=1)
        db.add(record)
        await db.flush()

        with pytest.raises(HTTPException) as exc:
            await cabinet_service.create_indexing_batch(
                db, cabinet_id=cabinet.id, project_id=seed["project"].id,
                document_type_id=seed["doc_type"].id, record_ids=[record.id],
                agent_id=seed["indexer"].id, supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 400
        assert record.batch_id is None

    async def test_document_type_from_another_project_is_refused(self, db: AsyncSession, seed):
        other = await _other_project(db, seed)
        foreign_type = DocumentType(project_id=other.id, name="Old form", json_schema={"type": "object"})
        db.add(foreign_type)
        cabinet = await _cabinet(db, seed)
        record = Record(cabinet_id=cabinet.id, status=RecordStatus.pending, current_version=1)
        db.add(record)
        await db.flush()

        with pytest.raises(HTTPException) as exc:
            await cabinet_service.create_indexing_batch(
                db, cabinet_id=cabinet.id, project_id=seed["project"].id,
                document_type_id=foreign_type.id, record_ids=[record.id],
                agent_id=seed["indexer"].id, supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 400
