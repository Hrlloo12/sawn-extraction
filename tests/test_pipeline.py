from app.extraction.pipeline import extract
from app.readers.base import Line, ReadResult
from app.readers.text_reader import read_text


def run(text):
    return extract(read_text(text))


def codes(output):
    return [w.code for w in output.warnings]


def test_full_text_ad():
    out = run("مكتب واحة التجربة للاستقدام\nترخيص رقم ١٠١٢٣٤٥\nجوال وواتساب: ٠٥٥ ٠٠٠ ٠١٠١")
    assert out.status == "ok"
    assert out.office_name.normalized_value == "مكتب واحة التجربة للاستقدام"
    assert out.office_name.evidence.line == 1
    assert out.license_number.normalized_value == "1012345"
    assert [(c.type, c.normalized_value) for c in out.contacts] == [
        ("mobile", "+966550000101"),
        ("whatsapp", "+966550000101"),
    ]
    assert out.contacts[0].raw_value == "٠٥٥ ٠٠٠ ٠١٠١"
    assert out.contacts[0].evidence.snippet == "جوال وواتساب: ٠٥٥ ٠٠٠ ٠١٠١"


def test_missing_office_name_keeps_other_fields():
    out = run("عاملات للتنازل\nللتواصل واتساب 0530000404")
    assert out.office_name is None
    assert out.status == "partial"
    assert "office_name_not_found" in codes(out)
    assert out.contacts[0].normalized_value == "+966530000404"


def test_ambiguous_number_is_not_guessed():
    out = run("مكتب نخيل التجربة للاستقدام\nاتصال: 05400?0505")
    assert out.status == "partial"
    assert out.contacts[0].normalized_value is None
    assert out.contacts[0].raw_value == "05400?0505"
    assert "ambiguous_phone" in codes(out)


def test_low_ocr_confidence_number_is_withheld():
    text = "اتصال: 0540000505"
    conf = [0.97] * 7 + [0.4] * 10
    read = ReadResult(
        input_type="image",
        lines=[
            Line(text="مكتب نخيل التجربة للاستقدام", source="ocr", line_no=1, char_confidence=[0.97] * 27),
            Line(text=text, source="ocr", line_no=2, char_confidence=conf, bbox=[1, 2, 3, 4, 5, 6, 7, 8]),
        ],
    )
    out = extract(read)
    contact = out.contacts[0]
    assert contact.normalized_value is None
    assert contact.evidence.ocr_confidence == 0.4
    assert contact.evidence.bbox == [1, 2, 3, 4, 5, 6, 7, 8]
    assert "ambiguous_phone" in codes(out)


def test_label_on_previous_line_sets_channel():
    out = run("مكتب درة التجربة للاستقدام\nواتساب:\n0570000303")
    assert [(c.type, c.normalized_value) for c in out.contacts] == [("whatsapp", "+966570000303")]


def test_support_number_in_pasted_text_is_unclear():
    out = run("مكتب سدرة التجربة للاستقدام\nواتساب: 0500000909\nللدعم الفني للمنصة: 920000111")
    attributions = {c.normalized_value: c.attribution for c in out.contacts}
    assert attributions == {"+966500000909": "advertiser", "+966920000111": "unclear"}
    assert "contact_attribution_unclear" in codes(out)


def test_ad_and_registration_numbers_are_not_contacts():
    out = run("مكتب أفق التجربة للاستقدام\nالسجل التجاري 1010000808\nرقم الإعلان 584512376\nجوال 0590000808")
    assert [c.normalized_value for c in out.contacts] == ["+966590000808"]


def test_multiple_office_names_warns():
    out = run("مكتب واحة التجربة للاستقدام\nبالتعاون مع شركة ساحل التجربة للاستقدام\nجوال 0550000101")
    assert out.office_name.normalized_value == "مكتب واحة التجربة للاستقدام"
    assert "multiple_office_names" in codes(out)


def test_whatsapp_label_after_last_number():
    out = run("التواصل على الرقم ٠٥٠١٢٣٤٥٦٧ واتس فقط")
    assert [(c.type, c.normalized_value) for c in out.contacts] == [("whatsapp", "+966501234567")]


def test_trailing_label_does_not_leak_to_earlier_number():
    out = run("جوال 0550000101 واتساب 0570000303")
    assert [(c.type, c.normalized_value) for c in out.contacts] == [
        ("mobile", "+966550000101"),
        ("whatsapp", "+966570000303"),
    ]


def test_handle_without_at_after_platform_label():
    out = run("سناب chat: diyar.demo\nتويتر: https://twitter.com/diyar_demo")
    assert [(c.type, c.platform, c.normalized_value) for c in out.contacts] == [
        ("social", "snapchat", "@diyar.demo"),
        ("social", "x", "@diyar_demo"),
    ]


def test_empty_content_is_not_found():
    out = run("عاملات منزلية متوفرة")
    assert out.status == "not_found"
