import re
from typing import TYPE_CHECKING

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.project import Project

_session = aioboto3.Session()


def _client_kwargs() -> dict:
    kwargs = dict(
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    if settings.aws_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_endpoint_url
    return kwargs


def _presigned_client_kwargs() -> dict:
    """Uses the public-facing endpoint so presigned URLs are browser-resolvable.
    Forces Signature V4 (required by MinIO)."""
    public_url = settings.aws_public_endpoint_url or settings.aws_endpoint_url
    kwargs = dict(
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        config=Config(signature_version="s3v4"),
    )
    if public_url:
        kwargs["endpoint_url"] = public_url
    return kwargs


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", name.lower())[:40].strip("-")


async def _create_bucket(bucket_name: str) -> bool:
    try:
        async with _session.client("s3", **_client_kwargs()) as client:
            if settings.aws_region == "us-east-1":
                await client.create_bucket(Bucket=bucket_name)
            else:
                await client.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": settings.aws_region},
                )
        return True
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            return True
        return False
    except Exception:
        return False


async def provision_org_bucket(db: AsyncSession, *, org: "Organization") -> None:
    from app.models.organization import OrgBucketStatus
    from app.models.tenant import Tenant

    tenant = await db.get(Tenant, org.tenant_id)
    bucket_name = f"docmate-{tenant.slug}-{_slugify(org.name)}"
    org.s3_bucket_name = bucket_name
    org.s3_bucket_status = OrgBucketStatus.provisioning

    ok = await _create_bucket(bucket_name)
    org.s3_bucket_status = OrgBucketStatus.ready if ok else OrgBucketStatus.error


async def provision_bucket(db: AsyncSession, *, project: "Project") -> None:
    from app.models.organization import Organization
    from app.models.project import S3BucketStatus

    org = await db.get(Organization, project.customer_org_id)
    if org and org.s3_bucket_name:
        project.s3_bucket_name = org.s3_bucket_name
        project.s3_bucket_status = S3BucketStatus.ready
    else:
        project.s3_bucket_status = S3BucketStatus.error


async def get_object_content_type(bucket: str, key: str) -> str:
    try:
        async with _session.client("s3", **_client_kwargs()) as client:
            resp = await client.head_object(Bucket=bucket, Key=key)
            return resp.get("ContentType", "application/octet-stream")
    except Exception:
        return "application/octet-stream"


async def sniff_content_type(bucket: str, key: str) -> str:
    try:
        async with _session.client("s3", **_client_kwargs()) as client:
            resp = await client.get_object(Bucket=bucket, Key=key, Range="bytes=0-7")
            header = await resp["Body"].read(8)
            if header[:5] == b"%PDF-":
                return "application/pdf"
            if header[:3] == b"\xff\xd8\xff":
                return "image/jpeg"
            if header[:4] == b"\x89PNG":
                return "image/png"
            if header[:4] in (b"II*\x00", b"MM\x00*"):
                return "image/tiff"
    except Exception:
        pass
    return "application/octet-stream"


async def get_object_bytes(bucket: str, key: str) -> bytes:
    async with _session.client("s3", **_client_kwargs()) as client:
        resp = await client.get_object(Bucket=bucket, Key=key)
        return await resp["Body"].read()


async def stream_object(bucket: str, key: str):
    """Yields an object's bytes for proxying through the backend on the API's
    own origin, instead of redirecting the browser to a presigned URL on a
    separate S3/MinIO origin. Avoids requiring the browser to establish TLS
    trust for a second hostname just to view a record image — see
    nginx/certs/generate.sh for why that's a real, silently-failing problem
    (background image/tile fetches to an untrusted origin have no
    user-actionable "proceed anyway" prompt the way top-level navigation
    does)."""
    async with _session.client("s3", **_client_kwargs()) as client:
        resp = await client.get_object(Bucket=bucket, Key=key)
        async for chunk in resp["Body"].iter_chunks():
            yield chunk


async def get_presigned_upload_url(bucket: str, key: str, expires: int = 3600) -> str:
    async with _session.client("s3", **_presigned_client_kwargs()) as client:
        return await client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )


async def get_presigned_view_url(
    bucket: str, key: str, expires: int = 3600, content_type: str | None = None
) -> str:
    async with _session.client("s3", **_presigned_client_kwargs()) as client:
        params: dict = {"Bucket": bucket, "Key": key, "ResponseContentDisposition": "inline"}
        if content_type:
            params["ResponseContentType"] = content_type
        return await client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires)
