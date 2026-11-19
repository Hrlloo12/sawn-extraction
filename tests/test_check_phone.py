import pytest

pytestmark = pytest.mark.team_data


def check(client, phone):
    return client.post("/check/phone", data={"phone": phone})


@pytest.mark.parametrize(
    "phone, office_id",
    [("0500000071", "E07"), ("+966500000111", "E11"), ("966500000031", "E03"), ("٠٥٠٠٠٠٠٠٣٢", "E03"), ("0110000151", "E15")],
)
def test_registered_number_returns_its_office(client, phone, office_id):
    body = check(client, phone).json()
    assert body["result"] == "registered" and body["registered"] is True
    assert body["matcher_result"] == "match" and body["verdict"] == "يعود"
    assert body["office"]["id"] == office_id
    assert body["office"]["name_ar"] in body["message"]


def test_local_and_international_forms_agree(client):
    local = check(client, "0500000071").json()
    intl = check(client, "+966500000071").json()
    for key in ("normalized_value", "result", "verdict", "office", "message"):
        assert local[key] == intl[key]


def test_unregistered_number_has_no_office(client):
    body = check(client, "0500006247").json()
    assert body["result"] == "unregistered" and body["registered"] is False
    assert body["office"] is None and body["report"] is None
    assert body["message"] == "الرقم غير مسجّل في القاعدة التجريبية"
    assert body["verdict"] == "لا نعرف"


def test_reported_number_names_the_impersonated_office(client):
    body = check(client, "0509990202").json()
    assert body["result"] == "reported" and body["office"] is None
    assert body["report"]["claimed_office"]["id"] == "E05"
    assert body["verdict"] == "لا يعود"


@pytest.mark.parametrize("phone, code", [("", "missing_value"), ("12345", "invalid_phone"), ("05000000", "invalid_phone")])
def test_invalid_input(client, phone, code):
    response = check(client, phone)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code
