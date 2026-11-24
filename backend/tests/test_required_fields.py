"""Required fields must hold a value on Submit/Done (required_fields + complete_task)."""
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DocumentType, Record, RecordVersion, Task, TaskStatus, TaskType
from app.services import required_fields, task_service

CASES = json.loads((Path(__file__).parent / "fixtures" / "required_fields_cases.json").read_text())


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
def test_shared_cases(case):
    schema = CASES["schemas"][case["schema"]]
    missing = required_fields.find_missing_required(schema, case["data"])
    assert sorted(".".join(str(s) for s in p) for p in missing) == sorted(case["missing"])


def test_describe_path_uses_titles_and_one_based_indexes():
    schema = CASES["schemas"]["caveat"]
    assert required_fields.describe_path(schema, ["registered_titles", 1, "folio_number"]) == (
        "Registered Titles › 2 › Folio Number"
    )


def test_no_schema_properties_means_nothing_required():
    assert required_fields.find_missing_required({"fields": [{"name": "title"}]}, {}) == []
    assert required_fields.find_missing_required(None, None) == []


async def _caveat_task(db: AsyncSession, seed, task_type: TaskType, user):
    doc_type = await db.get(DocumentType, seed["batch"].document_type_id)
    doc_type.json_schema = CASES["schemas"]["caveat"]
    await db.flush()
    task = await task_service.assign_task(
        db, record_id=seed["record"].id, batch_id=seed["batch"].id, task_type=task_type,
        agent_id=user.id, supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )
    await db.flush()
    await task_service.start_task(db, task_id=task.id, user_id=user.id, tenant_id=seed["tenant"].id)
    return task


@pytest.mark.parametrize("task_type,user_key", [(TaskType.indexing, "indexer"), (TaskType.qa, "qa_staff")])
async def test_complete_rejects_empty_required_list_and_changes_nothing(db, seed, task_type, user_key):
    user = seed[user_key]
    task = await _caveat_task(db, seed, task_type, user)
    version_before = (await db.get(Record, seed["record"].id)).current_version

    with pytest.raises(HTTPException) as exc:
        await task_service.complete_task(
            db, task_id=task.id, user_id=user.id, tenant_id=seed["tenant"].id,
            indexed_data={"caveat_number": "C1", "registered_titles": []},
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["missing"] == ["registered_titles"]
    assert "Registered Titles" in exc.value.detail["message"]

    assert (await db.get(Task, task.id)).status == TaskStatus.in_progress
    record = await db.get(Record, seed["record"].id)
    assert record.current_version == version_before
    assert record.locked_by == user.id


async def test_complete_accepts_filled_required_fields(db, seed):
    from sqlalchemy import select

    task = await _caveat_task(db, seed, TaskType.indexing, seed["indexer"])
    data = {"caveat_number": "C1", "registered_titles": [{"volume_number": "12", "folio_number": "400"}]}
    done = await task_service.complete_task(
        db, task_id=task.id, user_id=seed["indexer"].id, tenant_id=seed["tenant"].id, indexed_data=data,
    )
    assert done.status == TaskStatus.completed
    versions = (await db.execute(
        select(RecordVersion).where(RecordVersion.record_id == seed["record"].id)
    )).scalars().all()
    assert [v.indexed_data for v in versions] == [data]


async def test_save_progress_still_accepts_partial_data(client, db, seed):
    from tests.conftest import token

    await _caveat_task(db, seed, TaskType.indexing, seed["indexer"])
    await db.commit()
    resp = await client.patch(
        f"/api/records/{seed['record'].id}/draft",
        json={"indexed_data": {"registered_titles": []}},
        headers={"Authorization": f"Bearer {token(seed['indexer'])}", "X-Portal": "digitizing"},
    )
    assert resp.status_code == 200, resp.text
