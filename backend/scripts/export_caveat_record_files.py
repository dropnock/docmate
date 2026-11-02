"""
Exports up to N indexed records from one project as individual JSON files,
one per record, named <caveat_number>_<volume>_<folio>.json.

Each file holds just the record's indexed data, in the shape of the JSON
Schema of the document type it was indexed as (its batch's type): fields in
the schema's order, nested objects and arrays the same way ($ref definitions
resolved), and only fields that were entered — a field left blank is left
out, as the form leaves it out. Keys the schema doesn't define (e.g. fields
removed from the schema after the record was indexed) are dropped and listed
on stderr. No administrative details (record id, cabinet, status…).

The volume and folio in the name come from the record's first volume/folio
grouping — found the same way export_caveat_volume_folio finds it, as the
first top-level array of objects with a volume_number or folio_number key.
A record with several registered titles still gets one file, named after
its first; the rest are in the file. A record missing caveat_number, volume
or folio can't be named, so it's skipped and listed on stderr, as is one
that isn't in a batch (so has no document type). If two
records would get the same name, the later one gets _r<record id> appended
rather than overwriting the first.

Run inside the backend container/environment (needs direct DB access):
    docker compose exec backend python -m scripts.export_caveat_record_files --project-id 1 --count 100
    docker compose exec backend python -m scripts.export_caveat_record_files --project-id 1 --count 25 --random --status qa_passed --output-dir /tmp/caveats
Copy the files out of the container with:
    docker compose cp backend:/tmp/caveats ./caveats
"""
import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.batch import Batch
from app.models.document_type import DocumentType
from app.models.project import Project
from app.models.record import RecordStatus
from scripts.export_caveat_volume_folio import _find_groupings
from scripts.export_project_records_json import fetch_records

UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9.-]+")


def _part(value) -> str:
    """One piece of a filename: the value as text, with anything that isn't
    safe in a filename (slashes, spaces…) replaced by '-'."""
    return UNSAFE_CHARS.sub("-", str(value).strip()).strip("-.")


def file_stem(indexed_data: dict | None) -> tuple[str | None, str | None]:
    """(caveat_volume_folio, None), or (None, why the record can't be named)."""
    if not isinstance(indexed_data, dict):
        return None, "no indexed data"
    caveat = _part(indexed_data.get("caveat_number") or "")
    if not caveat:
        return None, "no caveat_number"
    groupings = _find_groupings(indexed_data)
    if not groupings:
        return None, "no volume/folio entered"
    first = groupings[0]
    volume, folio = _part(first.get("volume_number") or ""), _part(first.get("folio_number") or "")
    if not volume or not folio:
        return None, "first registered title is missing its volume or folio"
    return f"{caveat}_{volume}_{folio}", None


def _resolve(schema: dict, root: dict) -> dict:
    """Follows a local $ref ("#/definitions/address", "#/$defs/…")."""
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str) and schema["$ref"].startswith("#/"):
        target = root
        for part in schema["$ref"][2:].split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        schema = target
    return schema if isinstance(schema, dict) else {}


def shape(value, schema: dict, root: dict, path: str, dropped: list[str]):
    """value laid out as the schema describes it: object keys in the
    schema's property order (keys it doesn't define go to `dropped`),
    array items shaped by `items`. Values with no further structure in the
    schema are kept as entered."""
    schema = _resolve(schema, root)
    properties = schema.get("properties")
    if isinstance(value, dict) and isinstance(properties, dict):
        dropped.extend(f"{path}{k}" for k in value if k not in properties)
        return {
            key: shape(value[key], sub, root, f"{path}{key}.", dropped)
            for key, sub in properties.items()
            if key in value
        }
    items = schema.get("items")
    if isinstance(value, list) and isinstance(items, dict):
        return [shape(v, items, root, f"{path.rstrip('.')}[{i}].", dropped) for i, v in enumerate(value)]
    return value


async def _schemas_by_batch(db, batch_ids: set[int]) -> dict[int, dict]:
    if not batch_ids:
        return {}
    rows = (await db.execute(
        select(Batch.id, DocumentType.json_schema)
        .join(DocumentType, DocumentType.id == Batch.document_type_id)
        .where(Batch.id.in_(batch_ids))
    )).all()
    return {batch_id: schema for batch_id, schema in rows}


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project-id", type=int, required=True, help="The project to export from.")
    parser.add_argument("--count", "-n", type=int, required=True, help="How many records to export (at most).")
    parser.add_argument(
        "--status", action="append", choices=[s.value for s in RecordStatus], default=None,
        help="Restrict to one or more record statuses (repeatable).",
    )
    parser.add_argument("--random", action="store_true", help="A random sample instead of the first N by id.")
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Folder for the files (created if needed). Defaults to caveat_records_<project_id>_<timestamp>.",
    )
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be at least 1")

    async with AsyncSessionLocal() as db:
        if await db.get(Project, args.project_id) is None:
            print(f"Project {args.project_id} not found", file=sys.stderr)
            return 1
        records = await fetch_records(
            db,
            project_id=args.project_id,
            count=args.count,
            statuses=[RecordStatus(s) for s in args.status] if args.status else None,
            indexed_only=True,
            random=args.random,
        )
        schemas = await _schemas_by_batch(db, {r["batch_id"] for r in records if r["batch_id"]})

    out_dir = args.output_dir or Path(
        f"caveat_records_{args.project_id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    used: set[str] = set()
    written = 0
    skipped: list[tuple[int, str]] = []
    dropped_keys: list[tuple[str, list[str]]] = []
    for record in records:
        stem, reason = file_stem(record["indexed_data"])
        schema = schemas.get(record["batch_id"])
        if stem is None or schema is None:
            skipped.append((record["record_id"], reason or "not in a batch, so no document type"))
            continue
        if stem in used:
            stem = f"{stem}_r{record['record_id']}"
        used.add(stem)
        dropped: list[str] = []
        data = shape(record["indexed_data"], schema, schema, "", dropped)
        if dropped:
            dropped_keys.append((f"{stem}.json", dropped))
        (out_dir / f"{stem}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        written += 1

    print(f"Wrote {written} file(s) to {out_dir}/", file=sys.stderr)
    if skipped:
        print(f"{len(skipped)} record(s) skipped — couldn't be named:", file=sys.stderr)
        for record_id, reason in skipped:
            print(f"  - record {record_id}: {reason}", file=sys.stderr)
    if dropped_keys:
        print(f"{len(dropped_keys)} file(s) had fields the schema doesn't define, left out:", file=sys.stderr)
        for name, keys in dropped_keys:
            print(f"  - {name}: {', '.join(keys)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
