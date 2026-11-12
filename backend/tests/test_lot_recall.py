"""Recalling a released lot, removing records from a draft lot, and the
customer-organisation boundary on every lot endpoint."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aql import SamplingMode
from app.models.audit_log import AuditAction, AuditEntityType, AuditLog
from app.models.lot import LotRecord, LotStatus
from app.models.lot_inspection import LotInspection, LotInspectionStatus
from app.models.organization import Organization, OrgType
from app.models.user import Portal, User, UserRole
from app.services import lot_service
from tests.conftest import token
from tests.test_lot_inspections import (
    DE_HEADERS, _complete, _make_customer_supervisor, _make_lot, _release, _review_all, _start,
)


async def _inspect_pass(db, seed, lot):
    inspection = await _start(db, seed, lot)
    await _review_all(db, seed, lot, inspection)
    await _complete(db, seed, lot, inspection)
    return inspection


async def _released_lot(db, seed, num_records=3):
    lot, records = await _make_lot(db, seed, num_records=num_records)
    inspection = await _inspect_pass(db, seed, lot)
    await _release(db, seed, lot)
    return lot, records, inspection


async def _recall(db, seed, lot, reason="Wrong records included"):
    return await lot_service.recall_lot(
        db, lot_id=lot.id, reason=reason, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
    )


def _sup(seed):
    return {"Authorization": f"Bearer {token(seed['supervisor'])}", "X-Portal": DE_HEADERS}


def _cust(user):
    return {"Authorization": f"Bearer {token(user)}", "X-Portal": "customer"}


class TestRecall:
    async def test_recall_returns_lot_to_draft_and_supersedes_inspection(self, db: AsyncSession, seed):
        lot, _, inspection = await _released_lot(db, seed)
        await _recall(db, seed, lot)
        await db.flush()

        assert lot.status == LotStatus.draft
        assert lot.released_at is None and lot.released_by is None
        await db.refresh(inspection)
        assert inspection.status == LotInspectionStatus.superseded

        event = (await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == AuditEntityType.lot, AuditLog.entity_id == lot.id,
                AuditLog.action == AuditAction.recalled,
            )
        )).scalar_one()
        assert event.metadata_["reason"] == "Wrong records included"
        assert event.metadata_["superseded_inspection_id"] == inspection.id
        assert event.old_value["status"] == "released" and event.old_value["released_by"] == seed["supervisor"].id

    async def test_rerelease_needs_a_fresh_inspection(self, db: AsyncSession, seed):
        lot, _, _ = await _released_lot(db, seed)
        await _recall(db, seed, lot)
        with pytest.raises(HTTPException) as exc:
            await _release(db, seed, lot)
        assert exc.value.status_code == 409

        second = await _inspect_pass(db, seed, lot)
        assert second.attempt_number == 2
        await _release(db, seed, lot)
        assert lot.status == LotStatus.released

    async def test_cannot_recall_draft(self, db: AsyncSession, seed):
        lot, _ = await _make_lot(db, seed, num_records=3)
        with pytest.raises(HTTPException) as exc:
            await _recall(db, seed, lot)
        assert exc.value.status_code == 409

    async def test_cannot_recall_once_sampled(self, db: AsyncSession, seed):
        lot, _, _ = await _released_lot(db, seed)
        seed["aql_config"].sampling_mode = SamplingMode.manual
        await lot_service.apply_sample(
            db, lot_id=lot.id, sample_rate=0.5, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        with pytest.raises(HTTPException) as exc:
            await _recall(db, seed, lot)
        assert exc.value.status_code == 409
        assert lot.status == LotStatus.qc_in_progress

    async def test_cannot_sample_a_recalled_lot(self, db: AsyncSession, seed):
        lot, _, _ = await _released_lot(db, seed)
        await _recall(db, seed, lot)
        seed["aql_config"].sampling_mode = SamplingMode.manual
        with pytest.raises(HTTPException) as exc:
            await lot_service.apply_sample(
                db, lot_id=lot.id, sample_rate=0.5, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 409


class TestRecallApi:
    async def test_supervisor_recalls_over_api(self, db: AsyncSession, seed, client):
        lot, _, _ = await _released_lot(db, seed)
        await db.commit()
        resp = await client.post(f"/api/lots/{lot.id}/recall", json={"reason": "  typo in lot  "}, headers=_sup(seed))
        assert resp.status_code == 200
        assert resp.json()["status"] == "draft"

    async def test_blank_reason_rejected(self, db: AsyncSession, seed, client):
        lot, _, _ = await _released_lot(db, seed)
        await db.commit()
        resp = await client.post(f"/api/lots/{lot.id}/recall", json={"reason": "   "}, headers=_sup(seed))
        assert resp.status_code == 422

    async def test_customer_cannot_recall(self, db: AsyncSession, seed, client):
        lot, _, _ = await _released_lot(db, seed)
        cust = await _make_customer_supervisor(db, seed["cust_org"].id, seed)
        await db.commit()
        resp = await client.post(f"/api/lots/{lot.id}/recall", json={"reason": "x"}, headers=_cust(cust))
        assert resp.status_code == 403

    async def test_recalled_lot_disappears_for_customer_then_shows_recall_on_rerelease(
        self, db: AsyncSession, seed, client,
    ):
        lot, _, _ = await _released_lot(db, seed)
        cust = await _make_customer_supervisor(db, seed["cust_org"].id, seed)
        await db.commit()
        headers = _cust(cust)
        resp = await client.post(f"/api/lots/{lot.id}/recall", json={"reason": "Wrong box"}, headers=_sup(seed))
        assert resp.status_code == 200

        listed = await client.get(f"/api/lots/project/{seed['project'].id}", headers=headers)
        assert listed.status_code == 200 and listed.json() == []
        assert (await client.get(f"/api/lots/{lot.id}", headers=headers)).status_code == 404
        assert (await client.get(f"/api/lots/{lot.id}/inspection-report", headers=headers)).status_code == 404

        await _inspect_pass(db, seed, lot)
        await _release(db, seed, lot)
        await db.commit()

        listed = await client.get(f"/api/lots/project/{seed['project'].id}", headers=headers)
        assert [l["id"] for l in listed.json()] == [lot.id]
        history = (await client.get(f"/api/lots/{lot.id}/history", headers=headers)).json()
        actions = [e["action"] for e in history]
        assert actions[0] == "status_changed" and "recalled" in actions
        recall = next(e for e in history if e["action"] == "recalled")
        assert recall["metadata"]["reason"] == "Wrong box"


class TestRemoveRecords:
    async def test_remove_supersedes_passed_inspection(self, db: AsyncSession, seed):
        lot, records = await _make_lot(db, seed, num_records=3)
        inspection = await _inspect_pass(db, seed, lot)
        await lot_service.remove_lot_records(
            db, lot_id=lot.id, record_ids=[records[0].id],
            user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        await db.flush()
        remaining = (await db.execute(select(LotRecord.record_id).where(LotRecord.lot_id == lot.id))).scalars().all()
        assert sorted(remaining) == sorted(r.id for r in records[1:])
        await db.refresh(inspection)
        assert inspection.status == LotInspectionStatus.superseded
        event = (await db.execute(select(AuditLog).where(AuditLog.action == AuditAction.lot_records_removed))).scalar_one()
        assert event.entity_type == AuditEntityType.lot
        assert event.metadata_["record_ids"] == [records[0].id]
        assert event.new_value == {"record_count": 2}

    async def test_blocked_by_inspection_in_progress(self, db: AsyncSession, seed):
        lot, records = await _make_lot(db, seed, num_records=3)
        await _start(db, seed, lot)
        with pytest.raises(HTTPException) as exc:
            await lot_service.remove_lot_records(
                db, lot_id=lot.id, record_ids=[records[0].id],
                user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 409

    @pytest.mark.parametrize("pick", ["outsider", "all"])
    async def test_invalid_selection_rejected(self, db: AsyncSession, seed, pick):
        lot, records = await _make_lot(db, seed, num_records=2)
        ids = [seed["record"].id] if pick == "outsider" else [r.id for r in records]
        with pytest.raises(HTTPException) as exc:
            await lot_service.remove_lot_records(
                db, lot_id=lot.id, record_ids=ids, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 422

    async def test_only_from_draft(self, db: AsyncSession, seed):
        lot, records, _ = await _released_lot(db, seed)
        with pytest.raises(HTTPException) as exc:
            await lot_service.remove_lot_records(
                db, lot_id=lot.id, record_ids=[records[0].id],
                user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
            )
        assert exc.value.status_code == 409

    async def test_over_api(self, db: AsyncSession, seed, client):
        lot, records = await _make_lot(db, seed, num_records=3)
        await db.commit()
        resp = await client.post(
            f"/api/lots/{lot.id}/remove-records", json={"record_ids": [records[2].id]}, headers=_sup(seed),
        )
        assert resp.status_code == 200
        detail = (await client.get(f"/api/lots/{lot.id}", headers=_sup(seed))).json()
        assert len(detail["records"]) == 2


class TestCustomerOrgBoundary:
    """A tenant holds several customer organisations; one customer's
    supervisor must not reach another's lots, by listing or by id."""

    async def _outsider(self, db, seed):
        other_org = Organization(tenant_id=seed["tenant"].id, name="Other Cust", type=OrgType.customer)
        db.add(other_org)
        await db.flush()
        return await _make_customer_supervisor(db, other_org.id, seed, sub="sub-outsider"), other_org

    async def test_other_org_gets_404_everywhere(self, db: AsyncSession, seed, client):
        lot, records, _ = await _released_lot(db, seed)
        outsider, _ = await self._outsider(db, seed)
        await db.commit()
        h = _cust(outsider)
        calls = [
            client.get(f"/api/lots/project/{seed['project'].id}", headers=h),
            client.get(f"/api/lots/{lot.id}", headers=h),
            client.get(f"/api/lots/{lot.id}/qc-field-results", headers=h),
            client.post(f"/api/lots/{lot.id}/sample", json={"sample_rate": 0.5}, headers=h),
            client.post(f"/api/lots/{lot.id}/qc-batches", headers=h, json={
                "project_id": seed["project"].id, "document_type_id": seed["doc_type"].id,
                "assignments": [{"agent_id": seed["qc_agent"].id, "record_ids": [records[0].id]}],
            }),
            client.post(f"/api/lots/{lot.id}/send-for-remediation", headers=h),
            client.get(f"/api/records/{records[0].id}/history", headers=h),
        ]
        for call in calls:
            resp = await call
            assert resp.status_code == 404, (resp.request.method, resp.request.url.path, resp.status_code)

        # Nothing changed on the lot.
        await db.refresh(lot)
        assert lot.status == LotStatus.released and lot.sample_size is None

    async def test_own_customer_never_sees_drafts(self, db: AsyncSession, seed, client):
        draft, _ = await _make_lot(db, seed, num_records=2)
        cust = await _make_customer_supervisor(db, seed["cust_org"].id, seed)
        await db.commit()
        h = _cust(cust)
        assert (await client.get(f"/api/lots/project/{seed['project'].id}", headers=h)).json() == []
        assert (await client.get(f"/api/lots/{draft.id}", headers=h)).status_code == 404
        # The digitizing side still sees it.
        listed = (await client.get(f"/api/lots/project/{seed['project'].id}", headers=_sup(seed))).json()
        assert [l["id"] for l in listed] == [draft.id]


class TestQcBatchTargets:
    async def _sampled(self, db, seed):
        lot, records, _ = await _released_lot(db, seed, num_records=3)
        seed["aql_config"].sampling_mode = SamplingMode.manual
        await lot_service.apply_sample(
            db, lot_id=lot.id, sample_rate=1.0, user_id=seed["supervisor"].id, tenant_id=seed["tenant"].id,
        )
        return lot, records

    async def _create(self, db, seed, lot, record, **override):
        args = dict(
            project_id=seed["project"].id, document_type_id=seed["doc_type"].id,
            assignments=[{"agent_id": seed["qc_agent"].id, "record_ids": [record.id]}],
        )
        args.update(override)
        return await lot_service.create_qc_batches(
            db, lot_id=lot.id, supervisor_id=seed["supervisor"].id, tenant_id=seed["tenant"].id, **args,
        )

    async def test_valid_targets_accepted(self, db: AsyncSession, seed):
        lot, records = await self._sampled(db, seed)
        assert len(await self._create(db, seed, lot, records[0])) == 1

    async def test_other_project_rejected(self, db: AsyncSession, seed):
        lot, records = await self._sampled(db, seed)
        with pytest.raises(HTTPException) as exc:
            await self._create(db, seed, lot, records[0], project_id=seed["project"].id + 999)
        assert exc.value.status_code == 422

    async def test_foreign_document_type_rejected(self, db: AsyncSession, seed):
        from app.models.document_type import DocumentType
        from app.models.project import Project

        lot, records = await self._sampled(db, seed)
        other = Project(
            tenant_id=seed["tenant"].id, digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id, name="Other Project",
        )
        db.add(other)
        await db.flush()
        foreign = DocumentType(project_id=other.id, name="Elsewhere", json_schema={})
        db.add(foreign)
        await db.flush()
        with pytest.raises(HTTPException) as exc:
            await self._create(db, seed, lot, records[0], document_type_id=foreign.id)
        assert exc.value.status_code == 422

    async def test_other_orgs_agent_rejected(self, db: AsyncSession, seed):
        lot, records = await self._sampled(db, seed)
        other_org = Organization(tenant_id=seed["tenant"].id, name="Other Cust", type=OrgType.customer)
        db.add(other_org)
        await db.flush()
        stranger = User(
            tenant_id=seed["tenant"].id, organization_id=other_org.id, email="x@other.com",
            keycloak_sub="sub-stranger", full_name="Stranger",
            role=UserRole.customer_qc_agent, portal=Portal.customer, is_active=True,
        )
        db.add(stranger)
        await db.flush()
        with pytest.raises(HTTPException) as exc:
            await self._create(
                db, seed, lot, records[0],
                assignments=[{"agent_id": stranger.id, "record_ids": [records[0].id]}],
            )
        assert exc.value.status_code == 422
        assert str(stranger.id) in exc.value.detail
