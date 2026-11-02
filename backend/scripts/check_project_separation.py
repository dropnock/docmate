"""
Read-only check that one project's records and images are its own — for
tracking down records or images from another project (or from an earlier
deployment that used the same bucket) showing up in this one.

Every project of an organization shares the organization's bucket. Images
uploaded before project-scoped keys were stored under
cabinets/<cabinet_id>/<filename> — no project, and the same path whenever a
rebuilt database reuses a cabinet id — so anything that breaks the link
between a record, its cabinet and its project, or leaves old objects under a
cabinet's prefix, can mix projects up. (Uploads now go to
projects/<project_id>/cabinets/<cabinet_id>/<unique>/<filename>.) This reports:

  1. Batches of this project built from another project's cabinet (or the
     reverse), and batches using another project's document type.
  2. Records whose cabinet and batch belong to different projects.
  3. Records whose image key isn't under their own cabinet's prefix.
  4. Image keys shared by more than one record.
  5. source_identifiers (card filenames) that also exist in another project
     of the same organization — the same card ingested twice.
  6. From the bucket: objects under this project's cabinet prefixes that
     are older than the cabinet itself (left over from a previous
     deployment or database that used the same cabinet ids), records whose
     image object is older than the record, records whose image is missing,
     and objects no record points to.

Nothing is changed. --report writes every flagged id/key as JSON, which is
what a cleanup works from.

Run inside the backend container (needs DB + S3 access):
    docker compose exec backend python -m scripts.check_project_separation --project-id 1
    docker compose exec backend python -m scripts.check_project_separation --project-id 1 --report /tmp/separation.json
"""
import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, or_, select

from app.core.database import AsyncSessionLocal
from app.models.batch import Batch
from app.models.cabinet import Cabinet
from app.models.document_type import DocumentType
from app.models.project import Project
from app.models.record import Record
from app.services.s3_service import _client_kwargs, _session, key_belongs_to

# An upload writes the object a moment before the record row (and a TIFF's
# PDF a moment after), so only a gap bigger than this counts as "older".
CLOCK_SLACK = timedelta(minutes=10)
SHOW = 10  # examples printed per finding; --report has them all


def _cabinet_prefixes(project_id: int, cabinet_id: int) -> list[str]:
    """Where a cabinet's images live: the current per-project layout and the
    legacy cabinets/<id>/ one (see s3_service.record_image_key)."""
    return [f"projects/{project_id}/cabinets/{cabinet_id}/", f"cabinets/{cabinet_id}/"]


def _cabinet_of(key: str) -> int:
    parts = key.split("/")
    return int(parts[3] if parts[0] == "projects" else parts[1])


def _stem(key: str) -> str:
    """A record's key without its extension, so files derived from it (the
    PDF of a TIFF, OCR's searchable PDF or saved reply) count as its own."""
    for suffix in (".searchable.pdf", ".docai.json"):
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key.rsplit(".", 1)[0]


async def _list_objects(bucket: str, prefix: str) -> dict[str, object]:
    """{key: LastModified} for every object under prefix."""
    objects = {}
    async with _session.client("s3", **_client_kwargs()) as client:
        paginator = client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                objects[obj["Key"]] = obj["LastModified"]
    return objects


def _show(title: str, items: list, fmt) -> None:
    mark = "OK " if not items else "!! "
    print(f"{mark}{title}: {len(items)}")
    for item in items[:SHOW]:
        print(f"     {fmt(item)}")
    if len(items) > SHOW:
        print(f"     … and {len(items) - SHOW} more (see --report)")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--report", type=Path, default=None, help="Write every finding as JSON here.")
    parser.add_argument("--skip-s3", action="store_true", help="Database checks only; don't list the bucket.")
    args = parser.parse_args(argv)

    report: dict[str, list] = {}
    async with AsyncSessionLocal() as db:
        project = await db.get(Project, args.project_id)
        if project is None:
            print(f"Project {args.project_id} not found", file=sys.stderr)
            return 1
        cabinets = list((await db.execute(
            select(Cabinet).where(Cabinet.project_id == project.id).order_by(Cabinet.id)
        )).scalars())
        cabinet_ids = [c.id for c in cabinets]
        sibling_projects = list((await db.execute(
            select(Project.id, Project.name).where(
                Project.id != project.id,
                or_(Project.customer_org_id == project.customer_org_id,
                    Project.s3_bucket_name == project.s3_bucket_name),
            )
        )).all())

        print(f"Project {project.id} {project.name!r} — bucket {project.s3_bucket_name}, created {project.created_at:%Y-%m-%d}")
        print(f"Other projects sharing its organization or bucket: "
              f"{', '.join(f'{pid} {name!r}' for pid, name in sibling_projects) or 'none'}")
        for c in cabinets:
            n, first, last = (await db.execute(
                select(func.count(Record.id), func.min(Record.created_at), func.max(Record.created_at))
                .where(Record.cabinet_id == c.id)
            )).one()
            span = f", records created {first:%Y-%m-%d} → {last:%Y-%m-%d}" if n else ""
            print(f"  cabinet {c.id} {c.name!r}: created {c.created_at:%Y-%m-%d}, {n} records{span}")
        print()

        # 1. Batches crossing projects.
        rows = (await db.execute(
            select(Batch.id, Batch.project_id, Batch.cabinet_id, Cabinet.project_id, DocumentType.project_id)
            .outerjoin(Cabinet, Cabinet.id == Batch.cabinet_id)
            .join(DocumentType, DocumentType.id == Batch.document_type_id)
            .where(or_(Batch.project_id == project.id, Batch.cabinet_id.in_(cabinet_ids or [-1])))
        )).all()
        crossing = [
            {"batch_id": b, "batch_project": bp, "cabinet_id": cid, "cabinet_project": cp, "document_type_project": dp}
            for b, bp, cid, cp, dp in rows
            if (cp is not None and cp != bp) or dp != bp
        ]
        report["batches_crossing_projects"] = crossing
        _show("Batches whose cabinet or document type belongs to another project", crossing,
              lambda r: f"batch {r['batch_id']}: batch project {r['batch_project']}, cabinet {r['cabinet_id']} "
                        f"(project {r['cabinet_project']}), document type project {r['document_type_project']}")

        # 2. Records whose cabinet and batch disagree.
        rows = (await db.execute(
            select(Record.id, Record.cabinet_id, Cabinet.project_id, Record.batch_id, Batch.project_id)
            .outerjoin(Cabinet, Cabinet.id == Record.cabinet_id)
            .join(Batch, Batch.id == Record.batch_id)
            .where(or_(Record.cabinet_id.in_(cabinet_ids or [-1]), Batch.project_id == project.id))
        )).all()
        mixed = [
            {"record_id": r, "cabinet_id": c, "cabinet_project": cp, "batch_id": b, "batch_project": bp}
            for r, c, cp, b, bp in rows if cp != bp
        ]
        report["records_cabinet_batch_mismatch"] = mixed
        _show("Records whose cabinet and batch are in different projects", mixed,
              lambda r: f"record {r['record_id']}: cabinet {r['cabinet_id']} (project {r['cabinet_project']}), "
                        f"batch {r['batch_id']} (project {r['batch_project']})")

        records = list((await db.execute(
            select(Record.id, Record.cabinet_id, Record.file_reference, Record.source_identifier, Record.created_at)
            .where(Record.cabinet_id.in_(cabinet_ids or [-1]))
        )).all())

        # 3. Image keys outside the record's own cabinet prefix.
        outside = [
            {"record_id": r.id, "cabinet_id": r.cabinet_id, "file_reference": r.file_reference}
            for r in records
            if r.file_reference and not key_belongs_to(r.file_reference, project.id, r.cabinet_id)
        ]
        report["image_outside_own_cabinet"] = outside
        _show("Records whose image isn't under their own cabinet's prefix", outside,
              lambda r: f"record {r['record_id']} (cabinet {r['cabinet_id']}): {r['file_reference']}")

        # 4. Keys used by more than one record (in any project on this bucket).
        keys = [r.file_reference for r in records if r.file_reference]
        shared = []
        if keys:
            rows = (await db.execute(
                select(Record.file_reference, func.array_agg(Record.id))
                .join(Cabinet, Cabinet.id == Record.cabinet_id)
                .join(Project, Project.id == Cabinet.project_id)
                .where(Record.file_reference.in_(keys), Project.s3_bucket_name == project.s3_bucket_name)
                .group_by(Record.file_reference)
                .having(func.count(Record.id) > 1)
            )).all()
            shared = [{"file_reference": k, "record_ids": sorted(ids)} for k, ids in rows]
        report["image_shared_by_records"] = shared
        _show("Images used by more than one record", shared,
              lambda r: f"{r['file_reference']}: records {r['record_ids']}")

        # 5. Same card in another project of the organization.
        sibling_ids = [pid for pid, _ in sibling_projects]
        duplicates = []
        identifiers = {r.source_identifier for r in records if r.source_identifier}
        if identifiers and sibling_ids:
            rows = (await db.execute(
                select(Record.source_identifier, Cabinet.project_id, func.count(Record.id))
                .join(Cabinet, Cabinet.id == Record.cabinet_id)
                .where(Cabinet.project_id.in_(sibling_ids), Record.source_identifier.in_(identifiers))
                .group_by(Record.source_identifier, Cabinet.project_id)
            )).all()
            duplicates = [{"source_identifier": s, "other_project_id": p, "records": n} for s, p, n in rows]
        report["identifier_in_other_project"] = duplicates
        _show("Card identifiers also ingested in another project", duplicates,
              lambda r: f"{r['source_identifier']}: {r['records']} record(s) in project {r['other_project_id']}")

    # 6. The bucket itself.
    if not args.skip_s3 and project.s3_bucket_name:
        cabinet_created = {c.id: c.created_at for c in cabinets}
        objects: dict[str, object] = {}
        for cid in cabinet_ids:
            for prefix in _cabinet_prefixes(project.id, cid):
                objects.update(await _list_objects(project.s3_bucket_name, prefix))
        referenced = {r.file_reference for r in records if r.file_reference}
        # A TIFF's original stays beside its PDF; count it as referenced too.
        stems = {_stem(k) for k in referenced}

        older_than_cabinet = [
            {"key": k, "last_modified": m.isoformat(), "cabinet_id": _cabinet_of(k),
             "cabinet_created": cabinet_created[_cabinet_of(k)].isoformat()}
            for k, m in objects.items()
            if m < cabinet_created[_cabinet_of(k)] - CLOCK_SLACK
        ]
        report["objects_older_than_cabinet"] = older_than_cabinet
        _show("Bucket objects older than the cabinet they're filed under (left from a previous database/deployment)",
              older_than_cabinet, lambda r: f"{r['key']} — {r['last_modified'][:10]}, cabinet created {r['cabinet_created'][:10]}")

        stale_images, missing = [], []
        for r in records:
            if not r.file_reference:
                continue
            modified = objects.get(r.file_reference)
            if modified is None:
                if key_belongs_to(r.file_reference, project.id, r.cabinet_id):
                    missing.append({"record_id": r.id, "file_reference": r.file_reference})
            elif modified < r.created_at - CLOCK_SLACK:
                stale_images.append({"record_id": r.id, "file_reference": r.file_reference,
                                     "image_modified": modified.isoformat(), "record_created": r.created_at.isoformat()})
        report["record_image_older_than_record"] = stale_images
        report["record_image_missing"] = missing
        _show("Records pointing at an image older than the record itself", stale_images,
              lambda r: f"record {r['record_id']}: {r['file_reference']} — image {r['image_modified'][:10]}, "
                        f"record {r['record_created'][:10]}")
        _show("Records whose image is missing from the bucket", missing,
              lambda r: f"record {r['record_id']}: {r['file_reference']}")

        unreferenced = sorted(
            k for k in objects
            if k not in referenced and _stem(k) not in stems
        )
        report["objects_not_referenced"] = unreferenced
        _show("Bucket objects under this project's cabinets that no record uses", unreferenced, str)

    if args.report:
        args.report.write_text(json.dumps({"project_id": project.id, **report}, indent=2) + "\n")
        print(f"\nFull report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
