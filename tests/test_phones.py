import pytest

from app.extraction.normalize import normalize_text
from app.extraction.phones import find_phones


def phones(text):
    return find_phones(normalize_text(text).text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0550000101", "+966550000101"),
        ("٠٥٥٠٠٠٠١٠١", "+966550000101"),
        ("٠٥٥ ٠٠٠ ٠١٠١", "+966550000101"),
        ("۰۵۵۰۰۰۰۱۰۱", "+966550000101"),
        ("+966 55 000 0101", "+966550000101"),
        ("+966-55-000-0101", "+966550000101"),
        ("00966550000101", "+966550000101"),
        ("966550000101", "+966550000101"),
        ("550000101", "+966550000101"),
        ("(011) 456 0303", "+966114560303"),
        ("920000111", "+966920000111"),
        ("800 111 0000", "+9668001110000"),
    ],
)
def test_saudi_formats_normalize_to_e164(text, expected):
    hits = phones(f"رقم التواصل {text} شكرا")
    assert [h.e164 for h in hits] == [expected]


def test_types_follow_number_plan():
    hits = phones("0550000101 0114560303 8001110000")
    assert [h.base_type for h in hits] == ["mobile", "landline", "toll_free"]


def test_two_numbers_in_one_run_are_split():
    hits = phones("0550001010 / 0570002020 - 0114560303")
    assert [h.e164 for h in hits] == ["+966550001010", "+966570002020", "+966114560303"]


def test_unreadable_digit_is_ambiguous_not_guessed():
    hits = phones("اتصال: 05400?0505")
    assert len(hits) == 1
    assert hits[0].kind == "ambiguous"
    assert hits[0].e164 is None


def test_short_number_with_mobile_prefix_is_invalid():
    hits = phones("جوال 05500001")
    assert hits[0].kind == "invalid"
    assert hits[0].e164 is None


@pytest.mark.parametrize(
    "text",
    ["التكلفة 14500 ريال", "تاريخ 2025/03/14", "العمر 25-35", "أسعار من 9,500 ريال", "رقم 1234567"],
)
def test_non_phone_numbers_are_ignored(text):
    assert phones(text) == []


def test_raw_value_maps_back_to_original_text():
    nt = normalize_text("جوال: ٠٥٥ ٠٠٠ ٠١٠١")
    hit = find_phones(nt.text)[0]
    assert nt.raw(hit.start, hit.end) == "٠٥٥ ٠٠٠ ٠١٠١"
