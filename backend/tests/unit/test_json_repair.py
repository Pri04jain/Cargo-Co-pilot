"""Tests for truncated-JSON repair in the extract node."""
import json

from app.pipeline.nodes.extract import _repair_truncated_json


BASE = json.dumps(
    {
        "packing_list_number": {"value": "PL-2024-0587", "confidence": 1.0, "evidence": None},
        "date": {"value": "2024-05-10", "confidence": 0.9, "evidence": None},
        "packages": [
            {
                "item_no": {"value": "1", "confidence": 1.0, "evidence": None},
                "description": {"value": "Machine parts", "confidence": 1.0, "evidence": None},
            },
            {"item_no": {"value": "2", "confidence": 1.0, "evidence": None}},
        ],
    },
    indent=2,
)


class TestRepairTruncatedJson:
    def test_untouched_when_not_truncated(self):
        assert _repair_truncated_json(BASE) == BASE

    def test_cut_mid_string_value(self):
        cut = BASE.index("Machine parts") + 7  # inside the value string
        repaired = _repair_truncated_json(BASE[:cut])
        data = json.loads(repaired)
        assert data["packing_list_number"]["value"] == "PL-2024-0587"
        assert data["date"]["value"] == "2024-05-10"
        # package 1 survives, but its in-progress 'description' is dropped
        assert data["packages"][0]["item_no"]["value"] == "1"

    def test_cut_mid_number_value(self):
        cut = BASE.index("0.9") + 2  # inside a confidence number
        repaired = _repair_truncated_json(BASE[:cut])
        data = json.loads(repaired)
        assert data["packing_list_number"]["value"] == "PL-2024-0587"

    def test_cut_inside_key(self):
        cut = BASE.index('"item_no"') + 5  # inside the key of a later member
        repaired = _repair_truncated_json(BASE[:cut])
        data = json.loads(repaired)
        assert data["date"]["value"] == "2024-05-10"

    def test_cut_at_partial_key_after_complete_value(self):
        # Truncation exactly after a key's closing quote (no colon yet)
        cut = BASE.index('"item_no"')
        repaired = _repair_truncated_json(BASE[:cut])
        data = json.loads(repaired)
        assert "date" in data

    def test_all_realistic_cut_offsets_parse(self):
        first_member_end = BASE.index(",") + 1
        bad = []
        for cut in range(first_member_end, len(BASE)):
            truncated = BASE[:cut]
            repaired = _repair_truncated_json(truncated)
            if repaired == truncated:
                continue  # not truncated at this offset
            try:
                json.loads(repaired)
            except json.JSONDecodeError:
                bad.append(cut)
        assert bad == []

    def test_escaped_quotes_inside_string(self):
        # \" sequences inside a string must not be treated as string ends
        doc = json.dumps(
            {
                "a": {"value": "first", "confidence": 1.0},
                "b": {"value": 'say "hi" now', "confidence": 1.0},
            },
            indent=2,
        )
        cut = doc.index("now") + 2  # inside the escaped-quoted value of "b"
        repaired = _repair_truncated_json(doc[:cut])
        data = json.loads(repaired)
        assert data["a"]["value"] == "first"
        assert "b" not in data or data["b"].get("value") in (None, 'say "hi" no')

    def test_nothing_salvageable_returns_content(self):
        # Truncation before any complete member: nothing to salvage
        doc = json.dumps({"a": {"value": "xyz", "confidence": 1.0}}, indent=2)
        cut = doc.index("xyz") + 1
        assert _repair_truncated_json(doc[:cut]) == doc[:cut]
