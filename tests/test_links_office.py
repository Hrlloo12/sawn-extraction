import pytest

from app.extraction.links import classify_url, find_handles, normalize_website
from app.extraction.normalize import normalize_text
from app.extraction.office import find_licenses, find_office_names


@pytest.mark.parametrize(
    "url,ctype,normalized,platform",
    [
        ("https://wa.me/966550000101", "whatsapp", "+966550000101", None),
        ("wa.me/+966550000101?text=hi", "whatsapp", "+966550000101", None),
        ("https://api.whatsapp.com/send?phone=966560000202&text=x", "whatsapp", "+966560000202", None),
        ("whatsapp://send?phone=966560000202", "whatsapp", "+966560000202", None),
        ("tel:+966550000101", "mobile", "+966550000101", None),
        ("tel:0114560303", "landline", "+966114560303", None),
        ("mailto:Info@Waha-Test.example?subject=x", "email", "info@waha-test.example", None),
        ("https://www.instagram.com/durra.test/?igshid=demo", "social", "@durra.test", "instagram"),
        ("https://snapchat.com/add/durra_test", "social", "@durra_test", "snapchat"),
        ("https://x.com/waha_test", "social", "@waha_test", "x"),
        ("https://t.me/waha_test", "social", "@waha_test", "telegram"),
        ("https://www.tiktok.com/@waha_test", "social", "@waha_test", "tiktok"),
        ("https://www.Waha-Test.example/offers/?utm_source=x&id=7", "website", "waha-test.example/offers?id=7", None),
    ],
)
def test_classify_url(url, ctype, normalized, platform):
    result = classify_url(url)
    assert (result.type, result.normalized, result.platform) == (ctype, normalized, platform)


def test_whatsapp_link_without_number_is_flagged():
    result = classify_url("https://wa.me/message/ABCDEF")
    assert result.type == "whatsapp" and result.normalized is None and result.problem == "unresolved_link"


def test_handle_platform_uses_nearest_keyword():
    found = find_handles("سناب: @waha_snap  انستا: @waha.insta")
    assert [(h[2], h[3]) for h in found] == [("waha_snap", "snapchat"), ("waha.insta", "instagram")]


def test_website_normalization_drops_tracking_only():
    assert normalize_website("http://sahil-test.example/?fbclid=1") == "sahil-test.example"


def names(text):
    return [c.normalized for c in find_office_names(normalize_text(text))]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("مكتب واحة التجربة للاستقدام", ["مكتب واحة التجربة للاستقدام"]),
        ("يعلن مكتب واحة التجربة للاستقدام عن توفر عاملات", ["مكتب واحة التجربة للاستقدام"]),
        ("شركة ساحل التجربة للاستقدام", ["شركة ساحل التجربة للاستقدام"]),
        ("مؤسسة درة التجربة للموارد البشرية", ["مؤسسة درة التجربة للموارد البشرية"]),
        ("مكتب استقدام الريادة التجريبي", ["مكتب استقدام الريادة التجريبي"]),
        ("المعلن: مكتب سدرة التجربة", ["مكتب سدرة التجربة"]),
        ("Bayan Trial Recruitment Office", ["Bayan Trial Recruitment Office"]),
        ("Licensed Bayan Trial Recruitment Company", ["Bayan Trial Recruitment Company"]),
        ("Al Noor Demo Manpower Co.", ["Al Noor Demo Manpower Co."]),
        ("مكتب الوفاء التجريبي لاستقدام العمالة المنزلية", ["مكتب الوفاء التجريبي لاستقدام العمالة المنزلية"]),
    ],
)
def test_office_name_patterns(text, expected):
    assert names(text)[: len(expected)] == expected


@pytest.mark.parametrize(
    "text",
    ["نحن مكتب مرخص للاستقدام", "متوفر عاملات من إندونيسيا للتنازل", "مكتب استقدام في الرياض", "المكتب: مفتوح من 9 إلى 5"],
)
def test_generic_phrases_are_not_names(text):
    assert names(text) == []


def licenses(text):
    return [h.normalized for h in find_licenses(normalize_text(text))]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ترخيص وزارة الموارد البشرية رقم ١٠١٢٣٤٥", ["1012345"]),
        ("ترخيص رقم: 5078901 جوال 0590000808", ["5078901"]),
        ("License No. 2034567", ["2034567"]),
        ("Licensed by HRSD - License No: 4067890", ["4067890"]),
        ("مكتب مرخص - جوال 0550000101", []),
        ("حاصل على ترخيص وزارة الموارد البشرية للتواصل 0550000101", []),
        ("السجل التجاري 1010000808", []),
    ],
)
def test_license_requires_explicit_keyword(text, expected):
    assert licenses(text) == expected
