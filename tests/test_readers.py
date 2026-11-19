import asyncio
import json

import httpx
import pytest

from app.readers.base import ReadError
from app.readers.image_reader import AzureReadProvider, lines_from_analyze_result, read_image
from app.readers.url_reader import parse_html, read_url
from scripts.sample_spec import SAMPLES
from tests.conftest import fake_resolver, make_settings


def run(coro):
    return asyncio.run(coro)


def read(url, settings, transport):
    return run(read_url(url, settings, transport, fake_resolver))


def error_code(url, settings, transport):
    with pytest.raises(ReadError) as info:
        read(url, settings, transport)
    return info.value.code


def test_redirect_is_followed_and_recorded(settings, demo_transport):
    result = read("http://localhost:8100/go/ad01_waha", settings, demo_transport)
    assert result.input_url == "http://localhost:8100/go/ad01_waha"
    assert result.final_url == "http://localhost:8100/ads/ad01_waha.html"


def test_sections_and_buttons_are_parsed(settings, demo_transport):
    result = read("http://localhost:8100/ads/ad02_sahil.html", settings, demo_transport)
    sections = {line.text: line.section for line in result.lines}
    assert sections["الدعم الفني: 920000111"] == "header"
    assert sections["خدمة العملاء 800 111 0000"] == "footer"
    assert sections["شركة ساحل التجربة للاستقدام"] == "listing"
    whatsapp = [link for link in result.links if "whatsapp" in link.href]
    assert whatsapp[0].text == "تواصل واتساب" and whatsapp[0].section == "listing"


@pytest.mark.parametrize(
    "url,code",
    [
        ("http://localhost:8100/private/ad01_waha", "login_required"),
        ("http://localhost:8100/members/ad01_waha", "login_required"),
        ("http://localhost:8100/broken", "fetch_failed"),
        ("http://localhost:8100/ads/missing.html", "fetch_failed"),
        ("http://localhost:8100/spa", "empty_page"),
        ("http://localhost:8100/escape-internal", "domain_not_allowed"),
        ("http://localhost:8100/escape-external", "domain_not_allowed"),
        ("http://localhost:8100/big", "response_too_large"),
        ("http://localhost:8100/slow", "timeout"),
        ("https://example.com/ad", "domain_not_allowed"),
        ("http://internal.sawn.test/ad", "blocked_address"),
        ("http://mapped.sawn.test/ad", "blocked_address"),
        ("ftp://localhost/ad", "invalid_url"),
        ("http://user:pass@localhost:8100/ads/ad01_waha.html", "invalid_url"),
    ],
)
def test_url_failures_return_clear_codes(url, code, settings, demo_transport):
    assert error_code(url, settings, demo_transport) == code


def test_localhost_blocked_when_not_allowed(demo_transport):
    strict = make_settings(allow_localhost=False)
    assert error_code("http://localhost:8100/ads/ad01_waha.html", strict, demo_transport) == "blocked_address"


def test_unreachable_host_is_fetch_failed():
    assert error_code("http://127.0.0.1:8199/ad", make_settings(fetch_timeout_seconds=8), None) == "fetch_failed"


def test_hidden_elements_and_scripts_are_ignored():
    page = """<html><body><main><p>مكتب واحة التجربة للاستقدام</p>
    <p style="display:none">جوال 0500001234</p><script>var x = "0500005678";</script></main></body></html>"""
    lines, _, _ = parse_html(page, "http://localhost/")
    assert [line.text for line in lines] == ["مكتب واحة التجربة للاستقدام"]


def test_label_and_linked_number_stay_on_one_line():
    page = '<html><body><main><p>جوال: <a href="tel:0550000101">0550000101</a></p></main></body></html>'
    lines, links, _ = parse_html(page, "http://localhost/")
    assert lines[0].text == "جوال: 0550000101"
    assert links[0].href == "tel:0550000101"


def test_analyze_result_lines_carry_polygon_and_confidence():
    fixture = json.loads((SAMPLES / "ocr" / "ad05_nakheel.json").read_text(encoding="utf-8"))
    lines = lines_from_analyze_result(fixture["analyzeResult"])
    assert lines[2].text == "اتصال: 05400?0505"
    assert lines[2].bbox and len(lines[2].bbox) == 8
    assert lines[2].confidence == pytest.approx(0.32)


def test_fixture_provider_reads_saved_image(settings):
    data = (SAMPLES / "images" / "ad01_waha.png").read_bytes()
    result = run(read_image(data, settings))
    assert result.lines[0].text == "مكتب واحة التجربة للاستقدام"
    assert result.lines[0].source == "ocr"


def test_new_image_without_fixture_needs_azure(settings):
    from PIL import Image
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(buffer, format="PNG")
    with pytest.raises(ReadError) as info:
        run(read_image(buffer.getvalue(), settings))
    assert info.value.code == "ocr_not_configured"


def test_azure_without_key_is_not_configured():
    settings = make_settings(ocr_provider="azure")
    data = (SAMPLES / "images" / "ad01_waha.png").read_bytes()
    with pytest.raises(ReadError) as info:
        run(read_image(data, settings))
    assert info.value.code == "ocr_not_configured"


def test_unsupported_and_large_files(settings):
    with pytest.raises(ReadError) as info:
        run(read_image(b"%PDF-1.4 not an image", settings))
    assert info.value.code == "unsupported_file"
    with pytest.raises(ReadError) as info:
        run(read_image(b"\x89PNG\r\n\x1a\n" + b"0" * 100, make_settings(max_image_bytes=50)))
    assert info.value.code == "file_too_large"


def azure_transport(final_status="succeeded"):
    fixture = json.loads((SAMPLES / "ocr" / "ad01_waha.json").read_text(encoding="utf-8"))
    calls = {"polls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Ocp-Apim-Subscription-Key"] == "fake-key"
        if request.method == "POST":
            assert "prebuilt-read:analyze?api-version=2024-11-30" in str(request.url)
            return httpx.Response(202, headers={"Operation-Location": "https://fake.cognitive.test/ops/1"})
        calls["polls"] += 1
        if calls["polls"] == 1:
            return httpx.Response(200, json={"status": "running"})
        if final_status == "failed":
            return httpx.Response(200, json={"status": "failed"})
        return httpx.Response(200, json={"status": "succeeded", "analyzeResult": fixture["analyzeResult"]})

    return httpx.MockTransport(handler), calls


def test_azure_rest_flow_with_mocked_service():
    settings = make_settings(ocr_provider="azure", azure_di_endpoint="https://fake.cognitive.test/", azure_di_key="fake-key")
    transport, calls = azure_transport()
    data = (SAMPLES / "images" / "ad01_waha.png").read_bytes()
    result = run(read_image(data, settings, AzureReadProvider(settings, transport)))
    assert calls["polls"] == 2
    assert result.lines[0].text == "مكتب واحة التجربة للاستقدام"


def test_azure_failed_analysis_is_reported():
    settings = make_settings(ocr_provider="azure", azure_di_endpoint="https://fake.cognitive.test/", azure_di_key="fake-key")
    transport, _ = azure_transport("failed")
    data = (SAMPLES / "images" / "ad01_waha.png").read_bytes()
    with pytest.raises(ReadError) as info:
        run(read_image(data, settings, AzureReadProvider(settings, transport)))
    assert info.value.code == "ocr_failed"
