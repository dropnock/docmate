"""Null-array report script (scripts/report_null_arrays.py), via scan()."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.record import Record, RecordStatus
from scripts import report_null_arrays as report

CAVEAT_SCHEMA = {
    "type": "object",
    "properties": {
        "caveat_number": {"type": "string"},
        "registered_titles": {"type": "array", "items": {"type": "object"}},
        "caveators": {"type": "array", "items": {"type": "object"}},
    },
}


async def _setup(db: AsyncSession, seed, cards: list[dict | None], status=RecordStatus.qa_passed):
    seed["doc_type"].json_schema = CAVEAT_SCHEMA
    records = [Record(batch_id=seed["batch"].id, status=status, current_version=1, indexed_data=c) for c in cards]
    db.add_all(records)
    await db.flush()
    return records


def _states(rows):
    return {(r["record_id"], r["field"], r["state"]) for r in rows}


class TestNullArrayReport:
    async def test_reports_null_missing_and_empty_arrays(self, db: AsyncSession, seed):
        ok, null, missing, empty, no_data = await _setup(db, seed, [
            {"caveat_number": "1", "registered_titles": [{}], "caveators": [{}]},
            {"caveat_number": "2", "registered_titles": [{}], "caveators": None},
            {"caveat_number": "3", "caveators": [{}]},
            {"caveat_number": "4", "registered_titles": [], "caveators": [{}]},
            None,
        ])
        rows, scanned, unbatched = await report.scan(db, seed["project"].id)

        assert scanned == 5 and unbatched == []
        assert _states(rows) == {
            (null.id, "caveators", "null"),
            (missing.id, "registered_titles", "missing"),
            (empty.id, "registered_titles", "empty"),
            (no_data.id, "(indexed_data)", "null"),
        }
        assert next(r for r in rows if r["record_id"] == null.id)["caveat_number"] == "2"

    async def test_field_and_null_only_filters(self, db: AsyncSession, seed):
        null, empty = await _setup(db, seed, [
            {"caveat_number": "2", "registered_titles": None, "caveators": None},
            {"caveat_number": "4", "registered_titles": [], "caveators": []},
        ])
        rows, _, _ = await report.scan(db, seed["project"].id, fields=["caveators"], null_only=True)
        assert _states(rows) == {(null.id, "caveators", "null")}

    async def test_only_qa_passed_records_of_the_project(self, db: AsyncSession, seed):
        await _setup(db, seed, [{"caveators": None}], status=RecordStatus.indexed)
        rows, scanned, _ = await report.scan(db, seed["project"].id)
        assert (rows, scanned) == ([], 0)
        rows, scanned, _ = await report.scan(db, seed["project"].id + 999)
        assert (rows, scanned) == ([], 0)
