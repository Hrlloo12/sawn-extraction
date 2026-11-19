from app.extraction.pipeline import extract
from app.readers.text_reader import read_text


def run(text: str):
    return extract(read_text(text))


def test_spaced_iban_is_normalized_and_checksum_checked():
    out = run("الدفع على حساب مكتب السدرة التجريبي للاستقدام SA40 0009 0000 0000 0000 0163")
    assert [i.normalized_value for i in out.ibans] == ["SA4000090000000000000163"]
    assert out.ibans[0].checksum_valid is True
    assert out.account_holder.normalized_value == "مكتب السدرة التجريبي للاستقدام"
    assert out.account_holder.holder_type == "organization"
    assert out.payment.payment_request.value is True
    assert out.payment.personal_account.value is False


def test_iban_digits_are_not_read_as_phone():
    out = run("حوّل على SA96 0088 0000 0000 0000 9001")
    assert out.contacts == []
    assert len(out.ibans) == 1


def test_arabic_digit_iban():
    arabic = "".join(chr(0x0660 + int(d)) for d in "4000090000000000000163")
    out = run("الدفع على SA" + arabic)
    assert out.ibans[0].normalized_value == "SA4000090000000000000163"


def test_bad_checksum_is_flagged_but_kept():
    out = run("الدفع على SA4100090000000000000163")
    assert out.ibans[0].normalized_value == "SA4100090000000000000163"
    assert out.ibans[0].checksum_valid is False
    assert "iban_checksum_invalid" in [w.code for w in out.warnings]


def test_short_iban_is_not_normalized():
    out = run("حوّل على SA4000090000000163")
    assert out.ibans[0].normalized_value is None
    assert "invalid_iban" in [w.code for w in out.warnings]


def test_person_holder_after_honorific_sets_personal_account():
    out = run("حوّل العربون على حساب الأستاذ خالد س. SA9600880000000000009001")
    assert out.account_holder.normalized_value == "خالد س."
    assert out.account_holder.holder_type == "person"
    assert out.payment.personal_account.value is True
    assert out.payment.personal_account.evidence[0].text == "خالد س."


def test_holder_after_bism():
    out = run("السداد على SA4200880000000000009003 باسم فهد ع.")
    assert out.account_holder.normalized_value == "فهد ع."


def test_new_account_claim_and_urgency():
    out = run("تنبيه: تم تحديث حسابنا البنكي، حوّلها اليوم على الحساب الجديد SA4200880000000000009100")
    assert out.payment.new_account_claim.value is True
    assert [p.text for p in out.payment.urgency_phrases] == ["تم تحديث حسابنا البنكي", "حوّلها اليوم"]


def test_new_phone_number_is_not_account_claim():
    out = run("مكتب التجربة للاستقدام رقمنا الجديد 0509990303")
    assert out.payment.new_account_claim.value is False


def test_platform_payment_is_not_payment_request():
    out = run("مكتب التجربة للاستقدام، التعاقد والدفع عبر منصة مساند فقط. تواصل 0500000011")
    assert out.payment.payment_request.value is False
    assert out.ibans == []


def test_neutral_text_has_no_urgency():
    out = run("مكتب التجربة للاستقدام: عروض استقدام مميزة، عاملة خلال أسبوعين")
    assert out.payment.urgency_phrases == []


def test_office_name_without_domain_word():
    out = run("تنبيه من شركة الضوء التجريبية للتشغيل: تواصل 0500000011")
    assert out.office_name.normalized_value == "شركة الضوء التجريبية للتشغيل"
    assert out.office_name.confidence == "medium"


def test_iban_counts_as_channel_for_status():
    out = run("مكتب التجربة للاستقدام، الدفع على SA4000090000000000000163")
    assert out.status == "ok"
