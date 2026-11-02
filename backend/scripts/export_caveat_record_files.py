"""
Exports up to N indexed records from one project as individual JSON files,
one per record, named <caveat_number>_<volume>_<folio>.json.

Each file holds the same JSON as export_project_records_json produces for a
record (identifiers, status, cabinet/batch and indexed_data).

The volume and folio in the name come from the record's first volume/folio
grouping — found the same way export_caveat_volume_folio finds it, as the
first top-level array of objects with a volume_number or folio_number key.
A record with several registered titles still gets one file, named after
its first; the rest are in the file. A record missing caveat_number, volume
or folio can't be named, so it's skipped and listed on stderr. If two
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

from app.core.database import AsyncSessionLocal
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

    out_dir = args.output_dir or Path(
        f"caveat_records_{args.project_id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    used: set[str] = set()
    written = 0
    skipped: list[tuple[int, str]] = []
    for record in records:
        stem, reason = file_stem(record["indexed_data"])
        if stem is None:
            skipped.append((record["record_id"], reason))
            continue
        if stem in used:
            stem = f"{stem}_r{record['record_id']}"
        used.add(stem)
        (out_dir / f"{stem}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        written += 1

    print(f"Wrote {written} file(s) to {out_dir}/", file=sys.stderr)
    if skipped:
        print(f"{len(skipped)} record(s) skipped — couldn't be named:", file=sys.stderr)
        for record_id, reason in skipped:
            print(f"  - record {record_id}: {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
