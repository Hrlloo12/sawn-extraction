from integration.demo_guards import apply_demo_guards
from integration.matcher_adapter import to_matcher_input
from scripts.run_team_cases import load_matcher, load_reference

MATCHER = load_matcher()
ENTITIES, REPORTS, _ = load_reference()


def contact(ctype, value, source):
    return {"type": ctype, "normalized_value": value, "attribution": "advertiser", "source": source}


def decide(response):
    mi = to_matcher_input(response)
    entity, channels, verdict = MATCHER.check(mi, ENTITIES, REPORTS)
    return verdict, apply_demo_guards(mi, entity, channels, verdict, ENTITIES, REPORTS)


def test_t02_misread_domain_from_image_becomes_unknown():
    response = {
        "office_name": {"normalized_value": "مكتب سنا الشروق للاستقدام"},
        "contacts": [contact("website", "sana-shuroog.example", "ocr"), contact("mobile", "+966500000032", "ocr")],
    }
    verdict, guarded = decide(response)
    assert verdict == "لا يعود"
    assert guarded["verdict"] == "لا نعرف"
    assert guarded["rule"] == "ocr_domain_mismatch"
    assert "sana-shuroog.example" in guarded["action"]


def test_t03_no_valid_channel_becomes_unknown():
    response = {"office_name": {"normalized_value": "مكتب مرفا السكينة للاستقدام"}, "contacts": [], "ibans": [{"normalized_value": None}]}
    verdict, guarded = decide(response)
    assert verdict == "يعود"
    assert guarded["verdict"] == "لا نعرف"
    assert guarded["rule"] == "no_valid_channel"


def test_same_lookalike_domain_from_text_keeps_matcher_verdict():
    response = {
        "office_name": {"normalized_value": "مكتب سنا الشروق للاستقدام"},
        "contacts": [contact("website", "sana-shuroog.example", "text"), contact("mobile", "+966500000032", "text")],
    }
    verdict, guarded = decide(response)
    assert guarded["verdict"] == verdict == "لا يعود"
    assert guarded["rule"] is None


def test_reported_domain_from_image_keeps_not_belongs():
    response = {
        "office_name": {"normalized_value": "Ghaimat Najd Recruitment"},
        "contacts": [contact("website", "ghaimat-najd-sa.example", "ocr")],
    }
    verdict, guarded = decide(response)
    assert guarded["verdict"] == "لا يعود"


def test_other_failing_channel_keeps_not_belongs():
    response = {
        "office_name": {"normalized_value": "مكتب سنا الشروق للاستقدام"},
        "contacts": [contact("website", "sana-shuroog.example", "ocr")],
        "ibans": [{"normalized_value": "SA8500880000000000009005"}],
    }
    verdict, guarded = decide(response)
    assert guarded["verdict"] == "لا يعود"


def test_unconfirmed_channel_blocks_belongs():
    response = {
        "office_name": {"normalized_value": "مكتب دار الوفاق للاستقدام"},
        "contacts": [contact("mobile", "+966500000041", "ocr"), contact("mobile", None, "ocr") | {"raw_value": "0509990505"}],
    }
    verdict, guarded = decide(response)
    assert verdict == "يعود"
    assert guarded["verdict"] == "لا نعرف" and guarded["rule"] == "unconfirmed_channel"
    assert "0509990505" in guarded["action"]


def test_unconfirmed_only_asks_to_confirm():
    response = {"office_name": {"normalized_value": "مكتب دار الوفاق للاستقدام"}, "contacts": [contact("mobile", None, "ocr") | {"raw_value": "0500000041"}]}
    _, guarded = decide(response)
    assert guarded["rule"] == "no_valid_channel" and "0500000041" in guarded["action"]
