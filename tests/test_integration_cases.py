import json

import pytest

from scripts.sample_spec import ROOT

CASES = json.loads((ROOT / "integration" / "matching_cases.json").read_text(encoding="utf-8"))["cases"]
REFERENCE = json.loads((ROOT / "integration" / "reference_data.sample.json").read_text(encoding="utf-8"))


def live_output(client, case):
    demo = case["demo_input"]
    value = demo.get("value") or (ROOT / demo["value_file"]).read_text(encoding="utf-8")
    return client.post("/extract", data={"input_type": demo["input_type"], "value": value}).json()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case_channels_come_from_extraction(client, case):
    saved = json.loads((ROOT / case["extraction_output"]).read_text(encoding="utf-8"))
    live = live_output(client, case)
    for output in (saved, live):
        assert output["office_name"]["normalized_value"] == case["office_name"]
        extracted = {(c["type"], c["normalized_value"]) for c in output["contacts"]}
        for channel in case["channels"]:
            assert (channel["type"], channel["value"]) in extracted


def test_all_three_outcomes_are_covered():
    outcomes = {ch["expected"] for case in CASES for ch in case["channels"]}
    assert {"belongs", "not_belongs", "insufficient"} <= outcomes


def test_reference_data_is_synthetic():
    domains = [ch["value"].split("@")[1] for o in REFERENCE["offices"] for ch in o["channels"] if ch["type"] == "email"]
    assert all(d.endswith(".example") for d in domains)
