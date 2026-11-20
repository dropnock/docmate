"""GET /records/{id} previously_sent_back, and the version reason QA's
correction of a customer-rejected record gets. Regression for the QC/QA
"previously rejected" banners, which used current_version > 1 — true for
every indexed record, since the first indexing pass makes version 2."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Record, RecordStatus, RecordVersion, TaskType
from app.models.record_version import VersionReason
from app.services import task_service
from tests.conftest import token


async def _run(db, seed, task_type, user, *, fail=False, data=None):
    task = await task_service.assign_task(
        db, record_id=seed["record"].id, batch_id=seed["batch"].id, task_type=task_type,
        agent_id=user.id, supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )
    await db.flush()
    await task_service.start_task(db, task_id=task.id, user_id=user.id, tenant_id=seed["tenant"].id)
    if fail:
        await task_service.fail_task(db, task_id=task.id, user_id=user.id, reason="Owner misspelt",
                                     tenant_id=seed["tenant"].id)
    else:
        await task_service.complete_task(db, task_id=task.id, user_id=user.id, tenant_id=seed["tenant"].id,
                                         indexed_data=data)
    await db.commit()


async def _sent_back(client, seed) -> dict:
    resp = await client.get(
        f"/api/records/{seed['record'].id}",
        headers={"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": "digitizing"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestPreviouslySentBack:
    async def test_freshly_indexed_record_is_not_sent_back(self, db: AsyncSession, seed, client):
        await _run(db, seed, TaskType.indexing, seed["indexer"], data={"title": "Doc A"})
        body = await _sent_back(client, seed)
        assert body["current_version"] == 2  # why current_version can't drive the banner
        assert body["previously_sent_back"] is False

    async def test_qa_rejection_marks_record_sent_back(self, db: AsyncSession, seed, client):
        await _run(db, seed, TaskType.indexing, seed["indexer"], data={"title": "Doc A"})
        await _run(db, seed, TaskType.qa, seed["qa_staff"], fail=True)
        assert (await _sent_back(client, seed))["previously_sent_back"] is True

    async def test_correcting_a_customer_rejection_is_versioned_as_such(self, db: AsyncSession, seed, client):
        await _run(db, seed, TaskType.indexing, seed["indexer"], data={"title": "Doc A"})
        record = await db.get(Record, seed["record"].id)
        record.status = RecordStatus.qc_failed  # what fail_task leaves after a customer QC rejection
        await db.commit()
        await _run(db, seed, TaskType.qa, seed["qa_staff"], data={"title": "Doc A (corrected)"})

        reasons = list((await db.execute(
            select(RecordVersion.reason).where(RecordVersion.record_id == record.id)
            .order_by(RecordVersion.version_number)
        )).scalars())
        assert reasons == [VersionReason.initial_indexing, VersionReason.rework_after_customer_rejection]
        assert (await _sent_back(client, seed))["previously_sent_back"] is True
