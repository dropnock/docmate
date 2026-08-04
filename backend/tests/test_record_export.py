"""Supervisor bulk export of records to a ZIP of per-record JSON
(record_service.export_records_zip)."""
import io
import json
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import record_service
from tests.conftest import token


class TestExportRecordsZip:
    async def test_zip_contains_one_json_per_record_with_current_data(self, db: AsyncSession, seed):
        seed["record"].indexed_data = {"title": "Doc A"}
        seed["record"].source_identifier = "scan-a"
        seed["record2"].indexed_data = {"title": "Doc B"}
        await db.flush()

        zip_bytes = await record_service.export_records_zip(
            db, record_ids=[seed["record"].id, seed["record2"].id]
        )

        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = sorted(zf.namelist())
        assert names == [f"record_{seed['record'].id}.json", f"record_{seed['record2'].id}.json"]

        payload_a = json.loads(zf.read(f"record_{seed['record'].id}.json"))
        assert payload_a["record_id"] == seed["record"].id
        assert payload_a["source_identifier"] == "scan-a"
        assert payload_a["indexed_data"] == {"title": "Doc A"}
        # Confirmed with user: the record's own data, not DocumentType.json_schema
        assert "json_schema" not in payload_a

    async def test_missing_record_raises_404(self, db: AsyncSession, seed):
        with pytest.raises(HTTPException) as exc_info:
            await record_service.export_records_zip(db, record_ids=[seed["record"].id, 999999])
        assert exc_info.value.status_code == 404


class TestExportRouter:
    async def test_returns_zip_content_type_and_disposition(self, db: AsyncSession, seed, client):
        supervisor_token = token(seed["supervisor"])
        resp = await client.post(
            "/api/records/export",
            json={"record_ids": [seed["record"].id]},
            headers={"Authorization": f"Bearer {supervisor_token}", "X-Portal": "digitizing"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]

    async def test_non_supervisor_role_gets_403(self, db: AsyncSession, seed, client):
        indexer_token = token(seed["indexer"])
        resp = await client.post(
            "/api/records/export",
            json={"record_ids": [seed["record"].id]},
            headers={"Authorization": f"Bearer {indexer_token}", "X-Portal": "digitizing"},
        )
        assert resp.status_code == 403
