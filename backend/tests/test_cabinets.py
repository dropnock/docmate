"""Cabinet ingestion, record management, and indexing batch creation tests."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Batch, BatchStatus, Record, RecordStatus, Task, TaskType
from app.models.cabinet import Cabinet
from app.services import cabinet_service
from tests.conftest import auth_headers


class TestCabinetIngestJson:
    async def test_ingest_creates_records(self, db: AsyncSession, seed):
        records = await cabinet_service.ingest_json_records(
            db,
            cabinet_id=seed["cabinet"].id,
            records_payload=[
                {"id": "DOC001", "title": "First Doc"},
                {"id": "DOC002", "title": "Second Doc"},
            ],
            id_field="id",
            user_id=seed["admin"].id,
            tenant_id=seed["tenant"].id,
        )
        assert len(records) == 2
        assert {r.source_identifier for r in records} == {"DOC001", "DOC002"}

    async def test_ingest_sets_indexed_data(self, db: AsyncSession, seed):
        records = await cabinet_service.ingest_json_records(
            db,
            cabinet_id=seed["cabinet"].id,
            records_payload=[{"id": "DOC003", "title": "Doc Three", "year": 2024}],
            id_field="id",
            user_id=seed["admin"].id,
            tenant_id=seed["tenant"].id,
        )
        assert records[0].indexed_data == {"id": "DOC003", "title": "Doc Three", "year": 2024}

    async def test_ingest_multiple_records_all_created(self, db: AsyncSession, seed):
        """Each payload item creates one record; two calls create two records."""
        await cabinet_service.ingest_json_records(
            db,
            cabinet_id=seed["cabinet"].id,
            records_payload=[{"id": "DOC004", "title": "First"}],
            id_field="id",
            user_id=seed["admin"].id,
            tenant_id=seed["tenant"].id,
        )
        await cabinet_service.ingest_json_records(
            db,
            cabinet_id=seed["cabinet"].id,
            records_payload=[{"id": "DOC005", "title": "Second"}],
            id_field="id",
            user_id=seed["admin"].id,
            tenant_id=seed["tenant"].id,
        )
        await db.flush()

        all_records = (await db.execute(
            select(Record).where(Record.cabinet_id == seed["cabinet"].id)
        )).scalars().all()
        assert len(all_records) == 2
        source_ids = {r.source_identifier for r in all_records}
        assert source_ids == {"DOC004", "DOC005"}

    async def test_ingest_via_api_requires_admin(self, client, seed):
        resp = await client.post(
            f"/api/cabinets/{seed['cabinet'].id}/ingest-json",
            json={"records": [{"id": "X1", "title": "Test"}], "id_field": "id"},
            headers=auth_headers(seed["indexer"]),
        )
        assert resp.status_code == 403

    async def test_ingest_via_api_admin_succeeds(self, client, seed):
        resp = await client.post(
            f"/api/cabinets/{seed['cabinet'].id}/ingest-json",
            json={"records": [{"id": "API001", "title": "Via API"}], "id_field": "id"},
            headers=auth_headers(seed["admin"]),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["created"] == 1


class TestCabinetRecordListing:
    async def test_list_cabinet_records(self, client, db, seed):
        # Ingest some records first
        await cabinet_service.ingest_json_records(
            db,
            cabinet_id=seed["cabinet"].id,
            records_payload=[
                {"id": "R1", "title": "Doc 1"},
                {"id": "R2", "title": "Doc 2"},
            ],
            id_field="id",
            user_id=seed["admin"].id,
            tenant_id=seed["tenant"].id,
        )
        await db.commit()

        resp = await client.get(
            f"/api/cabinets/{seed['cabinet'].id}/records",
            headers=auth_headers(seed["supervisor"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2

    async def test_list_cabinet_records_filter_by_status(self, client, db, seed):
        records = await cabinet_service.ingest_json_records(
            db,
            cabinet_id=seed["cabinet"].id,
            records_payload=[{"id": "S1"}, {"id": "S2"}],
            id_field="id",
            user_id=seed["admin"].id,
            tenant_id=seed["tenant"].id,
        )
        # Manually mark one as indexed
        records[0].status = RecordStatus.indexed
        await db.commit()

        resp = await client.get(
            f"/api/cabinets/{seed['cabinet'].id}/records?status=indexed",
            headers=auth_headers(seed["supervisor"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(r["status"] == "indexed" for r in data)

    async def test_get_cabinet_detail(self, client, seed):
        resp = await client.get(
            f"/api/cabinets/{seed['cabinet'].id}",
            headers=auth_headers(seed["supervisor"]),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Cabinet"

    async def test_list_cabinets_for_project(self, client, seed):
        resp = await client.get(
            f"/api/cabinets/project/{seed['project'].id}",
            headers=auth_headers(seed["supervisor"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any(c["id"] == seed["cabinet"].id for c in data)


class TestCreateIndexingBatch:
    async def test_create_indexing_batch_from_cabinet_records(self, db: AsyncSession, seed):
        # Create cabinet records to use
        cab_records = await cabinet_service.ingest_json_records(
            db,
            cabinet_id=seed["cabinet"].id,
            records_payload=[{"id": "B1"}, {"id": "B2"}, {"id": "B3"}],
            id_field="id",
            user_id=seed["admin"].id,
            tenant_id=seed["tenant"].id,
        )
        await db.flush()

        batch = await cabinet_service.create_indexing_batch(
            db,
            cabinet_id=seed["cabinet"].id,
            project_id=seed["project"].id,
            document_type_id=seed["doc_type"].id,
            record_ids=[r.id for r in cab_records],
            agent_id=seed["indexer"].id,
            supervisor_id=seed["supervisor"].id,
            tenant_id=seed["tenant"].id,
        )
        assert batch.id is not None
        assert batch.status in (BatchStatus.draft, BatchStatus.indexing, BatchStatus.submitted)

        # All records should now have tasks assigned
        tasks = (await db.execute(
            select(Task).where(
                Task.batch_id == batch.id,
                Task.task_type == TaskType.indexing,
            )
        )).scalars().all()
        assert len(tasks) == 3

    async def test_create_indexing_batch_via_api(self, client, db, seed):
        cab_records = await cabinet_service.ingest_json_records(
            db,
            cabinet_id=seed["cabinet"].id,
            records_payload=[{"id": "API_B1"}, {"id": "API_B2"}],
            id_field="id",
            user_id=seed["admin"].id,
            tenant_id=seed["tenant"].id,
        )
        await db.commit()

        resp = await client.post(
            f"/api/cabinets/{seed['cabinet'].id}/batches",
            json={
                "project_id": seed["project"].id,
                "document_type_id": seed["doc_type"].id,
                "record_ids": [r.id for r in cab_records],
                "agent_id": seed["indexer"].id,
                "batch_name": "API Test Batch",
            },
            headers=auth_headers(seed["supervisor"]),
        )
        assert resp.status_code == 201

    async def test_create_indexing_batch_indexer_forbidden(self, client, seed):
        resp = await client.post(
            f"/api/cabinets/{seed['cabinet'].id}/batches",
            json={
                "project_id": seed["project"].id,
                "document_type_id": seed["doc_type"].id,
                "record_ids": [],
                "agent_id": seed["indexer"].id,
            },
            headers=auth_headers(seed["indexer"]),
        )
        assert resp.status_code == 403
