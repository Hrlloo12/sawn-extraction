import json
from pathlib import Path

import httpx
import pytest

from tests.test_llm import gpt_client, openai_transport

IMAGES = Path("samples/team_images")
pytestmark = pytest.mark.team_images
FIELDS_OTHER_THAN_DOMAIN = ("office_name", "license_number", "ibans", "account_holder", "payment", "platform_contacts")


def post_image(client, name: str):
    with open(IMAGES / f"{name}.png", "rb") as handle:
        return client.post("/extract", data={"input_type": "image"}, files={"file": (f"{name}.png", handle, "image/png")})


def vision(*values, illegible=0):
    items = [{"value": v, "legible": True} for v in values] + [{"value": "", "legible": False}] * illegible
    return {"domains": items}


def ocr_text_provider(text: str):
    from app.readers.tesseract_reader import to_analyze_result

    class Provider:
        async def analyze(self, data):
            words = [[{"text": w, "left": 900 - 60 * i, "top": 10, "right": 950 - 60 * i, "bottom": 30, "conf": 95.0} for i, w in enumerate(line.split())] for line in text.split("\n")]
            return to_analyze_result(words)

    return Provider()


def use_ocr(client, text: str):
    from app.main import ReaderDeps, app, get_reader_deps

    deps = app.dependency_overrides[get_reader_deps]()
    provider = ocr_text_provider(text)
    app.dependency_overrides[get_reader_deps] = lambda: ReaderDeps(
        http_transport=deps.http_transport, resolver=deps.resolver, llm_transport=deps.llm_transport, ocr_provider=provider
    )
    return client


T02_OCR = "زوروا موقعنا الرسمي sana-shuroog.example أو اتصلوا على 0500000032.\nمكتب سنا الشروق للاستقدام يرحب بكم."


def test_vision_domain_replaces_ocr_domain_and_nothing_else(gpt_client):
    seen = []
    client = use_ocr(gpt_client(openai_transport(vision_payload=vision("sana-shurooq.example"), seen=seen), image_domain_reader="vision"), T02_OCR)
    body = post_image(client, "T02").json()
    request = json.loads(seen[0].content)
    assert len(seen) == 1
    assert request["input"][1]["content"][0]["type"] == "input_image"
    assert request["input"][1]["content"][0]["image_url"].startswith("data:image/png;base64,")
    assert body["extractor"]["domain_reader"] == "gpt_vision" and body["extractor"]["domain_status"] == "read"
    websites = [c for c in body["contacts"] if c["type"] == "website"]
    assert [(w["normalized_value"], w["source"]) for w in websites] == [("sana-shurooq.example", "ocr")]
    assert [c["normalized_value"] for c in body["contacts"] if c["type"] != "website"] == ["+966500000032"]

    baseline = use_ocr(gpt_client(openai_transport(), image_domain_reader="ocr"), T02_OCR)
    base = post_image(baseline, "T02").json()
    for field in FIELDS_OTHER_THAN_DOMAIN:
        assert body[field] == base[field], field
    assert [c for c in body["contacts"] if c["type"] != "website"] == [c for c in base["contacts"] if c["type"] != "website"]


def test_image_without_domain(gpt_client):
    client = use_ocr(gpt_client(openai_transport(vision_payload=vision()), image_domain_reader="vision"), "مكتب الأفق الرحب للاستقدام تواصل 0500000011")
    body = post_image(client, "T01").json()
    assert body["extractor"]["domain_status"] == "none_found"
    assert [c["type"] for c in body["contacts"]] == ["mobile"]
    assert not [w for w in body["warnings"] if w["code"].startswith("domain")]


def test_vision_failure_keeps_other_fields_and_asks_to_confirm_domain(gpt_client):
    client = use_ocr(gpt_client(openai_transport(status=500), image_domain_reader="vision"), T02_OCR)
    body = post_image(client, "T02").json()
    assert body["extractor"]["domain_status"] == "failed"
    website = [c for c in body["contacts"] if c["type"] == "website"][0]
    assert website["normalized_value"] is None and website["raw_value"] == "sana-shuroog.example"
    codes = [w["code"] for w in body["warnings"]]
    assert "domain_needs_confirmation" in codes and "domain_reader_unavailable" in codes
    assert [c["normalized_value"] for c in body["contacts"] if c["type"] == "mobile"] == ["+966500000032"]
    assert body["office_name"]["normalized_value"] == "مكتب سنا الشروق للاستقدام"


def test_illegible_domain_is_not_normalized(gpt_client):
    client = use_ocr(gpt_client(openai_transport(vision_payload=vision(illegible=1)), image_domain_reader="vision"), T02_OCR)
    body = post_image(client, "T02").json()
    assert body["extractor"]["domain_status"] == "illegible"
    website = [c for c in body["contacts"] if c["type"] == "website"][0]
    assert website["normalized_value"] is None
    assert "domain_needs_confirmation" in [w["code"] for w in body["warnings"]]


def test_vision_value_with_bad_format_is_rejected(gpt_client):
    client = use_ocr(gpt_client(openai_transport(vision_payload=vision("sana shurooq.example")), image_domain_reader="vision"), T02_OCR)
    body = post_image(client, "T02").json()
    assert all(c["normalized_value"] != "sana shurooq.example" for c in body["contacts"])


def test_vision_ignores_non_website_links(gpt_client):
    client = use_ocr(gpt_client(openai_transport(vision_payload=vision("wa.me/966500000099", "instagram.com/demo")), image_domain_reader="vision"), "مكتب الأفق الرحب للاستقدام تواصل 0500000011")
    body = post_image(client, "T01").json()
    assert [c["normalized_value"] for c in body["contacts"]] == ["+966500000011"]


def test_vision_not_called_for_unsupported_file(gpt_client):
    seen = []
    client = gpt_client(openai_transport(vision_payload=vision(), seen=seen), image_domain_reader="vision")
    response = client.post("/extract", data={"input_type": "image"}, files={"file": ("x.png", b"not an image", "image/png")})
    assert response.json()["error"]["code"] == "unsupported_file"
    assert seen == []


def test_email_domain_read_by_vision_is_not_a_website(gpt_client):
    text = "مكتب الأفق الرحب للاستقدام تواصل 0500000011 info@ufuq-demo.example"
    client = use_ocr(gpt_client(openai_transport(vision_payload=vision("ufuq-demo.example")), image_domain_reader="vision"), text)
    body = post_image(client, "T01").json()
    assert "website" not in [c["type"] for c in body["contacts"]]
    assert "email" in [c["type"] for c in body["contacts"]]
