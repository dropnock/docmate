"""lot_records: a record is in at most one lot

create_lot only checked qa_passed, so a record could be added to several
lots. Adds a unique index on lot_records.record_id. If the database already
has records in more than one lot, the upgrade stops and lists them; resolve
those by hand first (e.g. remove the record from a draft lot, or roll the
duplicate lot back with scripts/rollback_lot.py).

Revision ID: 76966634aa8c
Revises: a4e07dccd411
Create Date: 2026-11-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '76966634aa8c'
down_revision: Union[str, None] = 'a4e07dccd411'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dupes = op.get_bind().execute(sa.text(
        "SELECT record_id, string_agg(lot_id::text, ', ' ORDER BY lot_id) "
        "FROM lot_records GROUP BY record_id HAVING count(*) > 1 ORDER BY record_id"
    )).all()
    if dupes:
        listing = "; ".join(f"record {r} in lots {lots}" for r, lots in dupes[:50])
        raise RuntimeError(
            f"{len(dupes)} record(s) are in more than one lot — resolve before upgrading: {listing}"
        )
    op.create_unique_constraint("uq_lot_records_record_id", "lot_records", ["record_id"])


def downgrade() -> None:
    op.drop_constraint("uq_lot_records_record_id", "lot_records", type_="unique")
