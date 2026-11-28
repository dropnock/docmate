"""app.services.review_fields: the fields a reviewer marks, read from the
schema and the record's data. The frontend (shared/reviewFields.ts) follows
the same fixture."""
import json
from pathlib import Path

import pytest

from app.services import review_fields as rf

CASES = json.loads((Path(__file__).parent / "fixtures" / "review_fields_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_fields(case):
    assert rf.review_fields(case["schema"], case["data"]) == case["fields"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_labels_come_from_the_key_and_schema_alone(case):
    for f in case["fields"]:
        assert rf.label_for(case["schema"], f["key"]) == f["label"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_groups(case):
    for key, (group, label) in case["groups"].items():
        assert rf.group_key(key) == group
        assert rf.group_label(case["schema"], group) == label


def test_keys_round_trip():
    for path in (["a"], ["a.b", "c/d"], ["list", 3, "x~y"]):
        assert rf.parse_key(rf.make_key(path)) == path
