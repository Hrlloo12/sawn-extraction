import pytest

from scripts.sample_spec import FORMATS, SAMPLES, contact_key, expected_for, load_ads, page_url

ADS = load_ads()
CASES = [(ad, fmt) for ad in ADS for fmt in FORMATS]


def post(client, ad, fmt):
    if fmt == "text":
        text = (SAMPLES / "text" / f"{ad['id']}.txt").read_text(encoding="utf-8")
        return client.post("/extract", data={"input_type": "text", "value": text}).json()
    if fmt == "url":
        return client.post("/extract", data={"input_type": "url", "value": page_url(ad)}).json()
    with open(SAMPLES / "images" / f"{ad['id']}.png", "rb") as handle:
        return client.post("/extract", data={"input_type": "image"}, files={"file": ("ad.png", handle, "image/png")}).json()


@pytest.mark.parametrize("ad,fmt", CASES, ids=[f"{ad['id']}-{fmt}" for ad, fmt in CASES])
def test_sample_matches_expected(client, ad, fmt):
    body = post(client, ad, fmt)
    expected = expected_for(ad, fmt)
    office = body["office_name"]["normalized_value"] if body["office_name"] else None
    license_number = body["license_number"]["normalized_value"] if body["license_number"] else None
    assert body["status"] == expected["status"]
    assert office == expected["office_name"]
    assert license_number == expected["license_number"]
    assert sorted(map(contact_key, body["contacts"]), key=str) == sorted(map(contact_key, expected["contacts"]), key=str)
    assert sorted(map(contact_key, body["platform_contacts"]), key=str) == sorted(
        map(contact_key, expected["platform_contacts"]), key=str
    )
    got = {contact_key(c): c for c in body["contacts"]}
    for item in expected["contacts"]:
        actual = got[contact_key(item)]
        assert actual["attribution"] == item.get("attribution", "advertiser")
        if "platform" in item:
            assert actual["platform"] == item["platform"]
        assert actual["evidence"]["snippet"]
