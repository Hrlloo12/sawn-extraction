from scripts.sample_spec import SAMPLES

TOP_LEVEL_KEYS = {
    "schema_version",
    "request_id",
    "status",
    "input_type",
    "input_url",
    "final_url",
    "office_name",
    "license_number",
    "contacts",
    "platform_contacts",
    "warnings",
    "error",
    "timing_ms",
    "extractor",
    "ibans",
    "account_holder",
    "payment",
}
PAYMENT_KEYS = {"payment_request", "personal_account", "new_account_claim", "urgency_phrases"}
CONTACT_KEYS = {"type", "platform", "raw_value", "normalized_value", "source", "evidence", "attribution", "confidence"}
FIELD_KEYS = {"raw_value", "normalized_value", "source", "evidence", "confidence"}


def test_text_request_matches_contract(client):
    response = client.post(
        "/extract",
        data={"input_type": "text", "value": "مكتب واحة التجربة للاستقدام\nترخيص رقم 1012345\nواتساب 0550000101"},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == TOP_LEVEL_KEYS
    assert body["schema_version"] == "1.2"
    assert body["extractor"]["name"] == "rules" and body["extractor"]["model"] is None
    assert set(body["payment"]) == PAYMENT_KEYS
    assert body["ibans"] == [] and body["account_holder"] is None
    assert body["status"] == "ok"
    assert set(body["office_name"]) == FIELD_KEYS
    assert set(body["license_number"]) == FIELD_KEYS
    assert set(body["contacts"][0]) == CONTACT_KEYS
    assert body["contacts"][0]["normalized_value"] == "+966550000101"
    assert set(body["timing_ms"]) == {"read", "extract", "total"}


def test_empty_file_field_from_swagger_is_ignored(client):
    response = client.post("/extract", data={"input_type": "text", "value": "جوال 0550000101", "file": ""})
    assert response.status_code == 200


def test_image_request(client):
    with open(SAMPLES / "images" / "ad05_nakheel.png", "rb") as handle:
        response = client.post("/extract", data={"input_type": "image"}, files={"file": ("ad.png", handle, "image/png")})
    body = response.json()
    assert body["status"] == "partial"
    assert body["contacts"][0]["normalized_value"] is None
    assert body["contacts"][0]["source"] == "ocr"


def test_url_request_reports_unreadable_with_suggestion(client):
    body = client.post("/extract", data={"input_type": "url", "value": "http://localhost:8100/private/ad01_waha"}).json()
    assert body["status"] == "unreadable"
    assert body["error"] == {"code": "login_required", "message": "الصفحة تطلب تسجيل دخول", "suggestion": "upload_image"}
    assert body["input_url"] == "http://localhost:8100/private/ad01_waha"
    assert body["final_url"].startswith("http://localhost:8100/login")
    assert body["contacts"] == [] and body["office_name"] is None


def test_blank_image_is_not_found(client):
    with open(SAMPLES / "images" / "blank_pattern.png", "rb") as handle:
        body = client.post("/extract", data={"input_type": "image"}, files={"file": ("b.png", handle, "image/png")}).json()
    assert body["status"] == "not_found"


def test_invalid_requests_return_422(client):
    cases = [
        ({"input_type": "text"}, "missing_value"),
        ({"input_type": "url", "value": "  "}, "missing_value"),
        ({"input_type": "image"}, "missing_file"),
        ({"input_type": "pdf", "value": "x"}, "invalid_input_type"),
        ({"value": "x"}, "invalid_input_type"),
    ]
    for data, code in cases:
        response = client.post("/extract", data=data)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == code


def test_text_too_long(client, settings):
    response = client.post("/extract", data={"input_type": "text", "value": "ا" * (settings.max_text_chars + 1)})
    assert response.json()["detail"]["code"] == "text_too_long"


def test_openapi_documents_multipart_body(client):
    spec = client.get("/openapi.json").json()
    body = spec["paths"]["/extract"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert body["properties"]["file"]["format"] == "binary"
    assert body["properties"]["input_type"]["enum"] == ["text", "url", "image"]
    assert "ExtractResponse" in spec["components"]["schemas"]


def test_health(client):
    body = client.get("/health").json()
    assert body == {
        "status": "ok",
        "schema_version": "1.2",
        "ocr_provider": "fixture",
        "azure_configured": False,
        "extractor": "rules",
        "image_domain_reader": "ocr",
        "openai_configured": False,
        "openai_model": "gpt-5-mini",
        "vision_model": "gpt-5-mini",
    }


def test_cors_preflight(client):
    response = client.options(
        "/extract",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 200
