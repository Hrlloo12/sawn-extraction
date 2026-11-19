import json

import httpx
import pytest

from app.main import ReaderDeps, app, get_reader_deps
from tests.conftest import fake_resolver

AD = "مكتب الواحة التجريبي للاستقدام - عرض خاص! حوّل العربون على حساب الأستاذ خالد س. SA96 0088 0000 0000 0000 9001 وواتساب 0509990101"


def llm_payload(**overrides):
    data = {
        "office_name": {"value": "مكتب الواحة التجريبي للاستقدام", "quote": "مكتب الواحة التجريبي للاستقدام"},
        "license_number": {"value": None, "quote": None},
        "phones": [
            {"raw": "0509990101", "kind": "whatsapp", "role": "advertiser", "quote": "وواتساب 0509990101"},
            {"raw": "0559999999", "kind": "mobile", "role": "advertiser", "quote": "0559999999"},
        ],
        "urls": [],
        "emails": [],
        "ibans": [{"raw": "SA96 0088 0000 0000 0000 9001", "quote": "SA96 0088 0000 0000 0000 9001"}],
        "account_holder": {"value": "خالد س.", "holder_type": "person", "quote": "حساب الأستاذ خالد س."},
        "payment_request": {"value": True, "quotes": ["حوّل العربون"]},
        "personal_account": {"value": True, "quotes": ["حساب الأستاذ خالد س."]},
        "new_account_claim": {"value": False, "quotes": []},
        "urgency_phrases": ["عرض خاص", "احجز قبل الجميع"],
    }
    data.update(overrides)
    return data


def openai_reply(payload) -> httpx.Response:
    text = json.dumps(payload, ensure_ascii=False)
    return httpx.Response(
        200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}
    )


def openai_transport(text_payload=None, vision_payload=None, seen=None, status=200, error_message="bad key"):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": error_message}})
        body = json.loads(request.content)
        is_vision = isinstance(body["input"][1]["content"], list)
        return openai_reply(vision_payload if is_vision else text_payload)

    return httpx.MockTransport(handler)


@pytest.fixture
def gpt_client(client, settings, demo_transport):
    def use(transport, **overrides):
        settings.openai_api_key = "sk-test-not-real"
        settings.extractor = "gpt"
        for key, value in overrides.items():
            setattr(settings, key, value)
        app.dependency_overrides[get_reader_deps] = lambda: ReaderDeps(
            http_transport=demo_transport, resolver=fake_resolver, llm_transport=transport
        )
        return client

    return use


def post_text(client, text=AD, **extra):
    return client.post("/extract", data={"input_type": "text", "value": text, **extra})


def test_gpt_output_is_normalized_and_grounded(gpt_client):
    seen = []
    body = post_text(gpt_client(openai_transport(llm_payload(), seen=seen))).json()
    request = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/responses"
    assert request["model"] == "gpt-5-mini"
    assert request["text"]["format"]["strict"] is True
    assert body["extractor"]["name"] == "gpt" and body["extractor"]["model"] == "gpt-5-mini"
    assert body["extractor"]["fallback_reason"] is None
    assert body["office_name"]["normalized_value"] == "مكتب الواحة التجريبي للاستقدام"
    assert [(c["type"], c["normalized_value"]) for c in body["contacts"]] == [("whatsapp", "+966509990101")]
    assert body["ibans"][0]["normalized_value"] == "SA9600880000000000009001"
    assert body["account_holder"]["holder_type"] == "person"
    assert [p["text"] for p in body["payment"]["urgency_phrases"]] == ["عرض خاص"]
    dropped = {w["field"] for w in body["warnings"] if w["code"] == "llm_value_not_in_source"}
    assert dropped == {"phones", "urgency_phrases"}


def test_gpt_model_comes_from_settings(gpt_client):
    seen = []
    post_text(gpt_client(openai_transport(llm_payload(), seen=seen), openai_model="gpt-5"))
    assert json.loads(seen[0].content)["model"] == "gpt-5"


def test_gpt_failure_falls_back_to_rules_without_leaking_key(gpt_client):
    transport = openai_transport(status=401, error_message="Incorrect API key provided: sk-proj-abcdEFGH1234****wxyz")
    response = post_text(gpt_client(transport))
    body = response.json()
    assert response.status_code == 200
    assert body["extractor"]["requested"] == "gpt"
    assert body["extractor"]["name"] == "rules"
    assert body["extractor"]["fallback_reason"].startswith("llm_failed: OpenAI HTTP 401")
    assert "llm_fallback_rules" in [w["code"] for w in body["warnings"]]
    assert body["ibans"][0]["normalized_value"] == "SA9600880000000000009001"
    assert "abcdEFGH" not in response.text and "sk-test" not in response.text


def test_gpt_unreachable_falls_back_to_rules(gpt_client):
    def refuse(request):
        raise httpx.ConnectError("refused")

    body = post_text(gpt_client(httpx.MockTransport(refuse))).json()
    assert body["extractor"]["name"] == "rules"
    assert body["extractor"]["fallback_reason"].startswith("llm_failed")
    assert body["office_name"]["normalized_value"] == "مكتب الواحة التجريبي للاستقدام"


def test_gpt_without_key_falls_back_to_rules(client, settings):
    settings.extractor = "gpt"
    body = post_text(client).json()
    assert body["extractor"]["name"] == "rules"
    assert body["extractor"]["fallback_reason"].startswith("llm_not_configured")


def test_rules_can_be_requested_for_text(gpt_client):
    seen = []
    body = post_text(gpt_client(openai_transport(llm_payload(), seen=seen)), extractor="rules").json()
    assert seen == []
    assert body["extractor"]["name"] == "rules"
    assert body["extractor"]["requested"] == "rules"
    assert body["extractor"]["fallback_reason"] is None


def test_invalid_extractor(client):
    response = post_text(client, extractor="bert")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_extractor"


def test_url_path_stays_on_rules(gpt_client):
    seen = []
    body = gpt_client(openai_transport(llm_payload(), seen=seen)).post(
        "/extract", data={"input_type": "url", "value": "http://localhost:8100/ads/ad01_waha.html"}
    ).json()
    assert seen == []
    assert body["extractor"]["name"] == "rules" and body["extractor"]["requested"] == "rules"


def test_gpt_name_is_trimmed_and_aligned_to_source_text():
    from app.extraction.llm import map_llm_output
    from app.readers.text_reader import read_text

    read = read_text("مكتب الواحة التجريبي للاستقدام فرع الشمال. اتصل")
    payload = llm_payload(phones=[], ibans=[], account_holder={"value": None, "holder_type": "none", "quote": None}, urgency_phrases=[])
    payload["office_name"] = {"value": "مكتب الواحة التجريبي للاستقداد الرسمي", "quote": None}
    out = map_llm_output(read, payload)
    assert out.office_name.normalized_value == "مكتب الواحة التجريبي للاستقدام"
    assert out.office_name.confidence == "high"
    assert "llm_value_aligned_to_source" in [w.code for w in out.warnings]


def test_redact_hides_openai_keys():
    from app.extraction.openai_client import redact

    assert redact("key sk-proj-ABCdef_123-xyz and sk-***") == "key sk-*** and sk-***"


def test_gpt_social_handles_and_support_numbers_follow_text_contract():
    from app.extraction.llm import map_llm_output
    from app.readers.text_reader import read_text

    read = read_text("مكتب الواحة التجريبي للاستقدام\nسناب: waha_demo\nللدعم 920000222")
    payload = llm_payload(
        phones=[{"raw": "920000222", "kind": "toll_free", "role": "platform", "quote": "للدعم 920000222"}],
        ibans=[],
        account_holder={"value": None, "holder_type": "none", "quote": None},
        urgency_phrases=[],
        social_handles=[{"raw": "waha_demo", "platform": "snapchat", "role": "advertiser", "quote": "سناب: waha_demo"}],
    )
    out = map_llm_output(read, payload)
    assert [(c.type, c.normalized_value, c.attribution) for c in out.contacts] == [
        ("toll_free", "+966920000222", "unclear"),
        ("social", "@waha_demo", "advertiser"),
    ]
    assert out.contacts[1].platform == "snapchat"
    assert out.platform_contacts == []
