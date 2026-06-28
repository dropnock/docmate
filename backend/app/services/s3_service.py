import re
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.project import Project


def _get_client():
    kwargs = dict(
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    if settings.aws_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_endpoint_url
    return boto3.client("s3", **kwargs)


def _get_presigned_client():
    """Client used only for generating presigned URLs — uses the public-facing endpoint
    so the URLs are resolvable by browsers rather than internal Docker hostnames."""
    public_url = settings.aws_public_endpoint_url or settings.aws_endpoint_url
    kwargs = dict(
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    if public_url:
        kwargs["endpoint_url"] = public_url
    return boto3.client("s3", **kwargs)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", name.lower())[:40].strip("-")


def _create_bucket(bucket_name: str) -> bool:
    """Create the bucket. Returns True on success, False on error."""
    try:
        client = _get_client()
        if settings.aws_region == "us-east-1":
            client.create_bucket(Bucket=bucket_name)
        else:
            client.create_bucket(
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
    """Create the S3 bucket for a customer organisation and update the org record."""
    from app.models.organization import OrgBucketStatus
    from app.models.tenant import Tenant

    tenant = await db.get(Tenant, org.tenant_id)
    bucket_name = f"docmate-{tenant.slug}-{_slugify(org.name)}"
    org.s3_bucket_name = bucket_name
    org.s3_bucket_status = OrgBucketStatus.provisioning

    ok = _create_bucket(bucket_name)
    org.s3_bucket_status = OrgBucketStatus.ready if ok else OrgBucketStatus.error


async def provision_bucket(db: AsyncSession, *, project: "Project") -> None:
    """Inherit the customer org's bucket for this project (no separate bucket)."""
    from app.models.organization import Organization
    from app.models.project import S3BucketStatus

    org = await db.get(Organization, project.customer_org_id)
    if org and org.s3_bucket_name:
        project.s3_bucket_name = org.s3_bucket_name
        project.s3_bucket_status = S3BucketStatus.ready
    else:
        project.s3_bucket_status = S3BucketStatus.error


def get_presigned_upload_url(bucket: str, key: str, expires: int = 3600) -> str:
    client = _get_presigned_client()
    return client.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


def get_presigned_view_url(bucket: str, key: str, expires: int = 3600) -> str:
    client = _get_presigned_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )
