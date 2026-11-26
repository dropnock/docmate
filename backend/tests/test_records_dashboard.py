"""Records Management dashboard: the live counts of batches in indexing,
those held there by open flags, and flagged records."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Batch, BatchStatus, Record, RecordFlag, RecordStatus
from app.models.record_flag import FlagStatus
from app.services.analytics_service import records_dashboard
from tests.conftest import token


async def _batch(db, seed, status, n_records=2):
    b = Batch(project_id=seed["project"].id, document_type_id=seed["doc_type"].id, name=f"B {status.value}", status=status)
    db.add(b)
    await db.flush()
    recs = [Record(batch_id=b.id, status=RecordStatus.pending, current_version=1) for _ in range(n_records)]
    db.add_all(recs)
    await db.flush()
    return b, recs


def _flag(seed, record, batch, status=FlagStatus.open):
    return RecordFlag(
        tenant_id=seed["tenant"].id, project_id=seed["project"].id, record_id=record.id, batch_id=batch.id,
        status=status.value, reason="cut off", raised_by=seed["indexer"].id,
    )


async def test_counts(db: AsyncSession, seed):
    # seed["batch"] is in indexing with two records.
    blocked, blocked_recs = await _batch(db, seed, BatchStatus.indexing)
    _, closed_recs = await _batch(db, seed, BatchStatus.indexing)
    qa, qa_recs = await _batch(db, seed, BatchStatus.qa_review)
    db.add_all([
        _flag(seed, blocked_recs[0], blocked),
        _flag(seed, blocked_recs[1], blocked),          # two flags, still one batch
        _flag(seed, closed_recs[0], blocked, FlagStatus.resolved),  # closed flags don't block
        _flag(seed, closed_recs[1], blocked, FlagStatus.withdrawn),
        _flag(seed, qa_recs[0], qa),                    # open, but its batch is past indexing
    ])
    await db.flush()
    d = await records_dashboard(db, project_id=seed["project"].id)
    assert d["batches_being_indexed"] == 3
    assert d["batches_blocked_by_flags"] == 1
    assert d["records_flagged"] == 3


async def test_live_counts_ignore_the_date_range(db: AsyncSession, seed):
    from datetime import datetime, timedelta, timezone
    db.add(_flag(seed, seed["record"], seed["batch"]))
    await db.flush()
    future = datetime.now(timezone.utc) + timedelta(days=30)
    d = await records_dashboard(db, project_id=seed["project"].id, date_from=future)
    assert (d["batches_being_indexed"], d["batches_blocked_by_flags"], d["records_flagged"]) == (1, 1, 1)


async def test_other_projects_dont_count(db: AsyncSession, seed):
    from app.models.project import Project, S3BucketStatus
    other = Project(tenant_id=seed["tenant"].id, digitizing_org_id=seed["de_org"].id,
                    customer_org_id=seed["cust_org"].id, name="Other", s3_bucket_status=S3BucketStatus.ready)
    db.add(other)
    await db.flush()
    db.add(_flag(seed, seed["record"], seed["batch"]))
    await db.flush()
    d = await records_dashboard(db, project_id=other.id)
    assert (d["batches_being_indexed"], d["batches_blocked_by_flags"], d["records_flagged"]) == (0, 0, 0)


async def test_api(db: AsyncSession, seed, client):
    db.add(_flag(seed, seed["record"], seed["batch"]))
    await db.commit()
    resp = await client.get(
        "/api/analytics/records-dashboard", params={"project_id": seed["project"].id},
        headers={"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": "digitizing"},
    )
    assert resp.status_code == 200
    assert resp.json()["batches_blocked_by_flags"] == 1 and resp.json()["records_flagged"] == 1
