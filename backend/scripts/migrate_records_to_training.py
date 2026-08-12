"""
Copies a sample of records — image + indexed_data — from a source DocMate
environment into an existing cabinet in a target (e.g. training) environment.
The two environments are assumed to be separate deployments (separate
Postgres + separate S3/MinIO), reachable over the network from wherever this
script runs, but otherwise unrelated to each other or to this script's own
process environment.

This intentionally bypasses task_service/lock_service/version_service/
audit_service — it's a one-time cross-database data seed, not a workflow
transition inside a single running app, so CLAUDE.md's "never write
batch.status directly" rule (which governs the app's own service layer)
doesn't apply here. Copied records land with cabinet_id set and batch_id
left NULL — they don't belong to a target batch, so nothing tries to route
them through indexing/QA/QC state transitions.

Idempotent: a source record already present in the target cabinet (matched
by file_reference) is skipped on re-run, so an interrupted --confirm run can
just be re-run to pick up where it left off.

Connection info for each side is supplied as a dotenv file using the same
keys as backend/.env:
    POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_HOST, POSTGRES_PORT
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_ENDPOINT_URL, AWS_REGION
Copy your environment's real .env and point POSTGRES_HOST / AWS_ENDPOINT_URL
at whatever externally-reachable address that environment exposes them on —
these scripts don't read the process's own .env at all, only the two files
given on the command line.

Dry run by default — prints what would be copied and makes no changes.
Pass --confirm to actually write to the target DB and S3/MinIO bucket.

Run from an environment that can reach both Postgres and both S3/MinIO
endpoints (a jump host, VPN'd laptop, etc.) with the backend's venv active:

    python -m scripts.migrate_records_to_training \\
        --source-env ./source.env --target-env ./target.env \\
        --source-project-name "Acme Intake" \\
        --target-cabinet-id 42 \\
        --status qa_passed --limit 25                       # dry run

    python -m scripts.migrate_records_to_training \\
        --source-env ./source.env --target-env ./target.env \\
        --source-project-name "Acme Intake" \\
        --target-cabinet-id 42 \\
        --status qa_passed --limit 25 --confirm --report out.json
"""
import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aioboto3
from dotenv import dotenv_values
from sqlalchemy import func, or_, select
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.batch import Batch
from app.models.cabinet import Cabinet
from app.models.project import Project
from app.models.record import Record, RecordStatus
from app.services.s3_service import resolve_record_project


@dataclass
class EnvConfig:
    label: str
    database_url: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_endpoint_url: str | None
    aws_region: str

    @classmethod
    def load(cls, path: Path, *, label: str) -> "EnvConfig":
        if not path.exists():
            sys.exit(f"ERROR: {label} env file not found: {path}")
        values = dotenv_values(path)

        def get(key: str, default: str) -> str:
            return values.get(key) or default

        database_url = values.get("DATABASE_URL")
        if not database_url:
            database_url = URL.create(
                drivername="postgresql+asyncpg",
                username=get("POSTGRES_USER", "docmate"),
                password=get("POSTGRES_PASSWORD", "docmate"),
                host=get("POSTGRES_HOST", "localhost"),
                port=int(get("POSTGRES_PORT", "5432")),
                database=get("POSTGRES_DB", "docmate"),
            ).render_as_string(hide_password=False)

        return cls(
            label=label,
            database_url=database_url,
            aws_access_key_id=get("AWS_ACCESS_KEY_ID", "minioadmin"),
            aws_secret_access_key=get("AWS_SECRET_ACCESS_KEY", "minioadmin"),
            aws_endpoint_url=values.get("AWS_ENDPOINT_URL") or None,
            aws_region=get("AWS_REGION", "us-east-1"),
        )

    def s3_client_kwargs(self) -> dict:
        kwargs = dict(
            region_name=self.aws_region,
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
        )
        if self.aws_endpoint_url:
            kwargs["endpoint_url"] = self.aws_endpoint_url
        return kwargs

    def make_sessionmaker(self) -> async_sessionmaker:
        engine = create_async_engine(self.database_url, echo=False, pool_pre_ping=True)
        return async_sessionmaker(engine, expire_on_commit=False)


_s3_session = aioboto3.Session()


async def _get_object(env: EnvConfig, bucket: str, key: str) -> tuple[bytes, str]:
    async with _s3_session.client("s3", **env.s3_client_kwargs()) as client:
        resp = await client.get_object(Bucket=bucket, Key=key)
        data = await resp["Body"].read()
        content_type = resp.get("ContentType", "application/octet-stream")
        return data, content_type


async def _put_object(env: EnvConfig, bucket: str, key: str, data: bytes, content_type: str) -> None:
    async with _s3_session.client("s3", **env.s3_client_kwargs()) as client:
        await client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)


async def _resolve_source_project(
    db: AsyncSession, *, project_id: int | None, project_name: str | None, tenant_id: int | None
) -> Project:
    if project_id is not None:
        project = await db.get(Project, project_id)
        if not project:
            sys.exit(f"ERROR: no source project with id={project_id}")
        return project

    stmt = select(Project).where(Project.name == project_name)
    if tenant_id is not None:
        stmt = stmt.where(Project.tenant_id == tenant_id)
    matches = list((await db.execute(stmt)).scalars().all())
    if not matches:
        scope = f" in tenant {tenant_id}" if tenant_id is not None else ""
        sys.exit(f"ERROR: no source project named {project_name!r}{scope}")
    if len(matches) > 1:
        lines = "\n".join(f"  id={p.id} tenant_id={p.tenant_id}" for p in matches)
        sys.exit(
            f"ERROR: {len(matches)} source projects named {project_name!r} — disambiguate with "
            f"--source-project-id or --tenant-id:\n{lines}"
        )
    return matches[0]


async def _select_source_records(
    db: AsyncSession, *, project: Project, status: RecordStatus, limit: int, order: str
) -> list[Record]:
    cabinet_ids = list(
        (await db.execute(select(Cabinet.id).where(Cabinet.project_id == project.id))).scalars().all()
    )
    batch_ids = list(
        (await db.execute(select(Batch.id).where(Batch.project_id == project.id))).scalars().all()
    )
    conditions = []
    if cabinet_ids:
        conditions.append(Record.cabinet_id.in_(cabinet_ids))
    if batch_ids:
        conditions.append(Record.batch_id.in_(batch_ids))
    if not conditions:
        return []

    stmt = select(Record).where(or_(*conditions), Record.status == status)
    if order == "newest":
        stmt = stmt.order_by(Record.id.desc())
    elif order == "oldest":
        stmt = stmt.order_by(Record.id.asc())
    else:
        stmt = stmt.order_by(func.random())
    stmt = stmt.limit(limit)

    return list((await db.execute(stmt)).scalars().all())


async def _resolve_target_cabinet(db: AsyncSession, cabinet_id: int) -> tuple[Cabinet, Project]:
    cabinet = await db.get(Cabinet, cabinet_id)
    if not cabinet:
        sys.exit(f"ERROR: no target cabinet with id={cabinet_id}")
    project = await db.get(Project, cabinet.project_id)
    if not project:
        sys.exit(f"ERROR: target cabinet {cabinet_id} has no resolvable project")
    if not project.s3_bucket_name:
        sys.exit(f"ERROR: target project {project.id} ({project.name!r}) has no S3 bucket provisioned")
    return cabinet, project


async def _already_migrated_file_refs(db: AsyncSession, cabinet_id: int) -> set[str]:
    result = await db.execute(
        select(Record.file_reference).where(
            Record.cabinet_id == cabinet_id, Record.file_reference.is_not(None)
        )
    )
    return {row[0] for row in result.all()}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source-env", required=True, type=Path, help="Path to a dotenv file with source DB + S3 connection info."
    )
    parser.add_argument(
        "--target-env", required=True, type=Path, help="Path to a dotenv file with target DB + S3 connection info."
    )

    project_group = parser.add_mutually_exclusive_group(required=True)
    project_group.add_argument("--source-project-id", type=int)
    project_group.add_argument("--source-project-name")
    parser.add_argument(
        "--tenant-id", type=int, default=None, help="Disambiguate --source-project-name across tenants."
    )

    parser.add_argument(
        "--target-cabinet-id", required=True, type=int, help="Existing cabinet in the target environment."
    )
    parser.add_argument(
        "--status",
        required=True,
        choices=[s.value for s in RecordStatus],
        help="Only records with this status are eligible to be copied.",
    )
    parser.add_argument("--limit", required=True, type=int, help="Max number of records to copy.")
    parser.add_argument(
        "--order", choices=["newest", "oldest", "random"], default="newest", help="Selection order (default: newest)."
    )
    parser.add_argument(
        "--confirm", action="store_true", help="Actually copy. Without this flag, dry run only."
    )
    parser.add_argument(
        "--report", type=Path, default=None, help="Where to write the JSON report (auto-named if omitted)."
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    source_env = EnvConfig.load(args.source_env, label="source")
    target_env = EnvConfig.load(args.target_env, label="target")
    SourceSession = source_env.make_sessionmaker()
    TargetSession = target_env.make_sessionmaker()

    async with SourceSession() as source_db, TargetSession() as target_db:
        project = await _resolve_source_project(
            source_db,
            project_id=args.source_project_id,
            project_name=args.source_project_name,
            tenant_id=args.tenant_id,
        )
        cabinet, target_project = await _resolve_target_cabinet(target_db, args.target_cabinet_id)

        status = RecordStatus(args.status)
        candidates = await _select_source_records(
            source_db, project=project, status=status, limit=args.limit, order=args.order
        )
        already = await _already_migrated_file_refs(target_db, cabinet.id)
        to_copy = [r for r in candidates if r.file_reference not in already]
        skipped_existing = len(candidates) - len(to_copy)

        print(f"Source project: {project.name!r} (id={project.id}, tenant_id={project.tenant_id})")
        print(
            f"Target cabinet: id={cabinet.id} ({cabinet.name!r}), "
            f"project={target_project.name!r} (id={target_project.id}), bucket={target_project.s3_bucket_name}"
        )
        print(f"Status filter: {status.value}   Order: {args.order}   Limit: {args.limit}")
        print(f"Candidates found: {len(candidates)}   Already in target (skipped): {skipped_existing}")
        print(f"To copy: {len(to_copy)}")

        if not args.confirm:
            print("\nDry run only — no changes made. Re-run with --confirm to execute.")
            for r in to_copy:
                print(f"  would copy record id={r.id} file_reference={r.file_reference!r}")
            if args.report:
                _write_report(args.report, project, cabinet, target_project, to_copy, [], [], dry_run=True)
                print(f"Dry-run report written to {args.report}")
            return 0

        copied: list[dict] = []
        failures: list[dict] = []
        for record in to_copy:
            try:
                source_project = await resolve_record_project(record, source_db)
                if not source_project or not source_project.s3_bucket_name or not record.file_reference:
                    failures.append({"source_record_id": record.id, "error": "no resolvable source bucket/key"})
                    continue

                data, content_type = await _get_object(
                    source_env, source_project.s3_bucket_name, record.file_reference
                )
                await _put_object(target_env, target_project.s3_bucket_name, record.file_reference, data, content_type)

                new_record = Record(
                    cabinet_id=cabinet.id,
                    batch_id=None,
                    file_reference=record.file_reference,
                    original_filename=record.original_filename,
                    source_identifier=record.source_identifier,
                    indexed_data=record.indexed_data,
                    current_version=1,
                    status=record.status,
                )
                target_db.add(new_record)
                await target_db.flush()
                copied.append({"source_record_id": record.id, "target_record_id": new_record.id})
                await target_db.commit()
                print(f"  copied record id={record.id} -> target id={new_record.id}")
            except Exception as exc:
                await target_db.rollback()
                failures.append({"source_record_id": record.id, "error": str(exc)})
                print(f"  FAILED record id={record.id}: {exc}", file=sys.stderr)

        print(f"\nCopied {len(copied)} of {len(to_copy)} record(s). {len(failures)} failure(s).")

        report_path = args.report or Path(
            f"migrate_report_{project.id}_to_cabinet{cabinet.id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        _write_report(report_path, project, cabinet, target_project, to_copy, copied, failures, dry_run=False)
        print(f"Report written to {report_path}")

        if failures:
            print(f"\nWARNING: {len(failures)} record(s) failed to copy:", file=sys.stderr)
            for f in failures:
                print(f"  - {f['source_record_id']}: {f['error']}", file=sys.stderr)
            return 1

    return 0


def _write_report(
    path: Path,
    project: Project,
    cabinet: Cabinet,
    target_project: Project,
    to_copy: list[Record],
    copied: list[dict],
    failures: list[dict],
    *,
    dry_run: bool,
) -> None:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "source_project": {"id": project.id, "name": project.name, "tenant_id": project.tenant_id},
        "target_cabinet": {"id": cabinet.id, "name": cabinet.name, "project_id": target_project.id},
        "candidates": [r.id for r in to_copy],
        "copied": copied,
        "failures": failures,
    }
    path.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
