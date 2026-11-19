import json

import pytest

from integration.matcher_adapter import to_matcher_input
from scripts.run_team_cases import REF, run

pytestmark = pytest.mark.team_data


def test_reference_pack_is_untouched_matcher_agrees_with_itself():
    from scripts.run_team_cases import load_matcher, load_reference

    matcher = load_matcher()
    entities, reports, cases = load_reference()
    assert len(cases) == 28
    for case in cases:
        _, _, verdict = matcher.check(case["expected_extraction"], entities, reports)
        assert verdict == case["expected_verdict"]


def test_rules_extraction_feeds_matcher(client):
    def post(case):
        return client.post("/extract", data={"input_type": "text", "value": case["ad_text"]}).json()

    summary, results = run(post, "pytest", "pytest", save_results=False)
    assert summary["verdicts_ok"] == summary["cases"] == 28
    assert summary["decisions_ok"] == 28
    assert summary["extraction_cases_ok"] == 28
    assert summary["fields_ok"] == 224


def test_adapter_formats():
    response = {
        "office_name": {"normalized_value": "مكتب التجربة للاستقدام"},
        "contacts": [
            {"type": "mobile", "normalized_value": "+966500000011", "attribution": "advertiser"},
            {"type": "whatsapp", "normalized_value": "+966500000011", "attribution": "advertiser"},
            {"type": "landline", "normalized_value": "+966110000011", "attribution": "unclear"},
            {"type": "website", "normalized_value": "office-test.example/contact", "attribution": "advertiser"},
            {"type": "mobile", "normalized_value": None, "attribution": "advertiser"},
        ],
        "platform_contacts": [{"type": "mobile", "normalized_value": "+966500000999", "attribution": "platform"}],
        "ibans": [{"normalized_value": "SA4000090000000000000163"}, {"normalized_value": None}],
        "account_holder": None,
        "payment": {"personal_account": {"value": True}, "urgency_phrases": [{"text": "عرض خاص"}]},
    }
    out = to_matcher_input(response)
    assert out["phones"] == ["966500000011", "966110000011"]
    assert out["urls"] == ["office-test.example"]
    assert out["ibans"] == ["SA4000090000000000000163"]
    assert out["account_holder"] == "" and out["personal_account"] is True
    assert out["new_account_claim"] is False and out["urgency_phrases"] == ["عرض خاص"]
    assert to_matcher_input(response, include_unclear=False)["phones"] == ["966500000011"]


def test_adapter_output_matches_schema():
    schema = json.loads((REF.parent / "integration" / "matcher_input.schema.json").read_text(encoding="utf-8"))
    out = to_matcher_input({})
    assert set(out) == set(schema["properties"])
    assert set(schema["required"]) <= set(out)
