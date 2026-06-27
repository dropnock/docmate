import re
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

if TYPE_CHECKING:
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


def _bucket_name(tenant_slug: str, project_name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", project_name.lower())[:40].strip("-")
    return f"docmate-{tenant_slug}-{slug}"


async def provision_bucket(db: AsyncSession, *, project: "Project") -> None:
    from app.models.project import S3BucketStatus
    from app.models.tenant import Tenant

    tenant = await db.get(Tenant, project.tenant_id)
    bucket_name = _bucket_name(tenant.slug, project.name)
    project.s3_bucket_name = bucket_name

    try:
        client = _get_client()
        if settings.aws_region == "us-east-1":
            client.create_bucket(Bucket=bucket_name)
        else:
            client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": settings.aws_region},
            )
        project.s3_bucket_status = S3BucketStatus.ready
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "BucketAlreadyOwnedByYou":
            project.s3_bucket_status = S3BucketStatus.ready
        else:
            project.s3_bucket_status = S3BucketStatus.error
    except Exception:
        project.s3_bucket_status = S3BucketStatus.error


def get_presigned_upload_url(bucket: str, key: str, expires: int = 3600) -> str:
    client = _get_client()
    return client.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


def get_presigned_view_url(bucket: str, key: str, expires: int = 3600) -> str:
    client = _get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )
