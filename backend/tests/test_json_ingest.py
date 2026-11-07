"""JSON ingest: every record must carry the identifier that links it to its
image, which may sit in a nested field; an image uploaded first gets the
data rather than a second, image-less record being created."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditAction, AuditLog
from app.models.cabinet import Cabinet
from app.models.record import Record
from tests.conftest import token


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {token(user)}", "X-Portal": "digitizing"}


async def _cabinet(db: AsyncSession, seed) -> Cabinet:
    cabinet = Cabinet(tenant_id=seed["tenant"].id, project_id=seed["project"].id, name="Titles")
    db.add(cabinet)
    await db.flush()
    return cabinet


async def _ingest(client, seed, cabinet, id_field, records):
    return await client.post(
        f"/api/cabinets/{cabinet.id}/ingest-json",
        json={"id_field": id_field, "records": records},
        headers=_auth(seed["admin"]),
    )


async def _records(db, cabinet) -> list[Record]:
    return list((await db.execute(
        select(Record).where(Record.cabinet_id == cabinet.id).order_by(Record.id)
    )).scalars())


class TestJsonIngest:
    async def test_missing_identifier_imports_nothing(self, client, db, seed):
        cabinet = await _cabinet(db, seed)
        resp = await _ingest(client, seed, cabinet, "id", [{"id": "a"}, {"md": {"title_name": "b"}}])
        assert resp.status_code == 422
        assert '1 of 2 record(s) have no "id" value (record 2)' in resp.json()["detail"]
        assert await _records(db, cabinet) == []

    async def test_nested_identifier(self, client, db, seed):
        cabinet = await _cabinet(db, seed)
        resp = await _ingest(client, seed, cabinet, "md.title_name", [{"md": {"title_name": " 1200_340 "}}])
        assert resp.status_code == 201, resp.text
        assert resp.json() == {"created": 1, "linked": 0}
        [record] = await _records(db, cabinet)
        assert record.source_identifier == "1200_340"

    async def test_duplicate_identifiers_rejected(self, client, db, seed):
        cabinet = await _cabinet(db, seed)
        resp = await _ingest(client, seed, cabinet, "id", [{"id": 7}, {"id": "7"}])
        assert resp.status_code == 422
        assert '"7" is in records 1 and 2' in resp.json()["detail"]

        db.add(Record(cabinet_id=cabinet.id, source_identifier="8", indexed_data={"id": "8"}))
        await db.flush()
        resp = await _ingest(client, seed, cabinet, "id", [{"id": "8"}])
        assert resp.status_code == 422
        assert '"8" already has data' in resp.json()["detail"]

    async def test_fills_record_of_image_uploaded_first(self, client, db, seed):
        cabinet = await _cabinet(db, seed)
        stub = Record(cabinet_id=cabinet.id, source_identifier="1200_340",
                      original_filename="1200_340.pdf", file_reference="k")
        db.add(stub)
        await db.flush()

        resp = await _ingest(client, seed, cabinet, "id", [{"id": "1200_340", "v": 1}, {"id": "9"}])
        assert resp.status_code == 201, resp.text
        assert resp.json() == {"created": 1, "linked": 1}

        records = await _records(db, cabinet)
        assert len(records) == 2
        await db.refresh(stub)
        assert stub.indexed_data == {"id": "1200_340", "v": 1}
        assert stub.file_reference == "k"
        actions = (await db.execute(
            select(AuditLog.action).where(AuditLog.entity_id == stub.id)
        )).scalars().all()
        assert AuditAction.data_imported in actions
