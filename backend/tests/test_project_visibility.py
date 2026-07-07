"""de_staff (indexer/QA) users only see projects they're rostered on."""
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import token


class TestProjectVisibility:
    async def test_de_staff_sees_only_assigned_projects(self, client, seed, db: AsyncSession):
        from app.models.project import Project, S3BucketStatus

        other_project = Project(
            tenant_id=seed["tenant"].id,
            digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id,
            name="Other Project",
            s3_bucket_status=S3BucketStatus.ready,
        )
        db.add(other_project)
        await db.commit()

        indexer_token = token(seed["indexer"])
        resp = await client.get(
            "/api/projects",
            headers={"Authorization": f"Bearer {indexer_token}"},
        )
        assert resp.status_code == 200
        project_ids = {p["id"] for p in resp.json()}
        assert seed["project"].id in project_ids
        assert other_project.id not in project_ids

    async def test_supervisor_sees_all_tenant_projects(self, client, seed, db: AsyncSession):
        from app.models.project import Project, S3BucketStatus

        other_project = Project(
            tenant_id=seed["tenant"].id,
            digitizing_org_id=seed["de_org"].id,
            customer_org_id=seed["cust_org"].id,
            name="Other Project",
            s3_bucket_status=S3BucketStatus.ready,
        )
        db.add(other_project)
        await db.commit()

        sup_token = token(seed["supervisor"])
        resp = await client.get(
            "/api/projects",
            headers={"Authorization": f"Bearer {sup_token}"},
        )
        assert resp.status_code == 200
        project_ids = {p["id"] for p in resp.json()}
        assert seed["project"].id in project_ids
        assert other_project.id in project_ids
