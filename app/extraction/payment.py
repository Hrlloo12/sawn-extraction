import re
import unicodedata
from dataclasses import dataclass

from app.extraction.normalize import NormalizedText, clean_spaces
from app.extraction.office import find_office_names

IBAN_RE = re.compile(r"(?<![A-Za-z0-9])SA(?:[ \-]?\d){22}(?!\d)", re.IGNORECASE)
IBAN_OCR_LIKE_RE = re.compile(r"(?<![A-Za-z0-9])(?:[S5$][A4]|\d{2})\d{2}(?:[ \-]\d{4}){5}(?!\d)")
IBAN_LOOSE_RE = re.compile(r"(?<![A-Za-z0-9])SA(?:[ \-]?[\d?*]){8,30}", re.IGNORECASE)

HONORIFICS = (
    r"(?:الأستاذة|الاستاذة|الأستاذ|الاستاذ|أستاذة|استاذة|أستاذ|استاذ|السيدة|السيد|الأخت|الاخت|الأخ|الاخ"
    r"|المندوبة|المندوب|الموظفة|الموظف|الشيخ|المهندس|الدكتور|أ\.|م\.|د\.)"
)
HOLDER_LEAD = re.compile(rf"(?<!\S)(?P<lead>باسم|بإسم|بأسم|لصالح|حساب)\s+(?:(?P<hon>{HONORIFICS})\s*)?")
NAME_TOKEN = re.compile(r"[^\W\d_]+\.?")
NAME_STOP = {
    "و", "أو", "او", "على", "عبر", "في", "من", "الى", "إلى", "رقم", "لدى", "بنك", "مصرف", "البنك", "المصرف",
    "الرسمي", "المعتاد", "الجديد", "البنكي", "الآيبان", "الايبان", "آيبان", "ايبان", "واتساب", "جوال", "للتواصل",
    "تواصل", "واتصل", "اتصل", "فقط", "وتواصل", "ثم", "بعد", "قبل", "مع",
}
ORG_WORDS = re.compile(r"مكتب|شركة|شركه|مؤسسة|مؤسسه|مركز|company|est\.?|establishment|recruitment", re.IGNORECASE)
PERSONAL_RE = re.compile(r"(?:ال)?حساب(?:ي|ه|ها)?\s+(?:ال)?شخصي|حساب\s+(?:ال)?موظف|حساب\s+(?:ال)?مندوب")

WORD_END = r"(?=$|[\s.،,!؟?:؛])"
TOKEN = r"[^\s.،,!؟?:؛]+"
PAY_RE = re.compile(
    r"(?<!\S)[وفل]?(?:حول(?:ها|ه|هم|وا|ي|و)?|تحويل|ادفع(?:ي|وا)?|الدفع|دفع|دفعة|السداد|سداد|سدد(?:ي|وا)?|تسدد|يسدد"
    rf"|رسوم|العربون|عربون|فاتورة|المبلغ|مبلغ){WORD_END}"
)
ACCOUNT_RE = re.compile(r"حساب|ايبان|آيبان|الآيبان|الايبان|iban", re.IGNORECASE)
ACCOUNT_NOUN = r"(?:ال)?(?:حساب\S*|ايبان|آيبان)(?:\s+(?:ال)?بنكي)?"
CHANGE_RE = re.compile(rf"(?:تم\s+)?(?:تغيير|تغير|تحديث|تعديل|استبدال)\s+(?:رقم\s+)?{ACCOUNT_NOUN}")
NEW_ACCOUNT_RE = re.compile(rf"{ACCOUNT_NOUN}\s+(?:ال)?جديد|حساب\s+جديد")

URGENCY_PATTERNS = [
    re.compile(r"عرض\s+(?:خاص|محدود)"),
    re.compile(rf"(?:ال)?عرض\s+(?:لأول|لاول)\s+\d+\s+{TOKEN}"),
    re.compile(r"(?:لفترة|لمدة)\s+محدودة"),
    re.compile(rf"(?:احجز|احجزي|احجزوا|سارع|سارعي|سارعوا|بادر|بادري|بادروا)\s+(?:الآن|الان|فورا|اليوم)(?:\s+قبل\s+(?:نفاد|نفاذ|انتهاء)\s+{TOKEN})?{WORD_END}"),
    re.compile(r"(?<!\S)قبل\s+(?:نفاد|نفاذ|انتهاء)\s+(?:الكمية|العرض|الأماكن|الاماكن|العمالة)"),
    re.compile(r"آخر\s+فرصة|اخر\s+فرصة|فرصة\s+أخيرة|فرصة\s+اخيرة|لا\s+تفوت"),
    re.compile(r"اليوم\s+فقط|خلال\s+\d+\s+(?:ساعة|ساعات)"),
    re.compile(
        rf"(?<!\S)(?:حول|احجز|نحجز|ادفع|سدد|ارسل|أرسل|اكمل|أكمل)(?:ها|ه|هم|وا|ي)?(?:\s+(?:لك|لكم))?\s+(?:اليوم|الآن|الان|فورا){WORD_END}"
    ),
    CHANGE_RE,
]


@dataclass
class IbanHit:
    start: int
    end: int
    normalized: str | None
    checksum_valid: bool
    problem: str | None


@dataclass
class PhraseHit:
    start: int
    end: int
    kind: str


@dataclass
class HolderHit:
    start: int
    end: int
    normalized: str
    holder_type: str
    lead: str


def strip_marks(nt: NormalizedText) -> NormalizedText:
    chars, index = [], []
    for ch, pos in zip(nt.text, nt.index):
        if unicodedata.category(ch) == "Mn":
            continue
        chars.append(ch)
        index.append(pos)
    return NormalizedText(original=nt.original, text="".join(chars), index=index)


def iban_checksum_ok(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    try:
        return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1
    except ValueError:
        return False


def find_ibans(text: str, ocr: bool = False) -> list[IbanHit]:
    hits = []
    taken = []
    for m in IBAN_RE.finditer(text):
        normalized = re.sub(r"[ \-]", "", m.group()).upper()
        ok = iban_checksum_ok(normalized)
        hits.append(IbanHit(m.start(), m.end(), normalized, ok, None if ok else "iban_checksum_invalid"))
        taken.append((m.start(), m.end()))
    for m in IBAN_LOOSE_RE.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        end = m.end()
        while end > m.start() and text[end - 1] in " -":
            end -= 1
        hits.append(IbanHit(m.start(), end, None, False, "invalid_iban"))
        taken.append((m.start(), end))
    if ocr:
        for m in IBAN_OCR_LIKE_RE.finditer(text):
            if not any(s <= m.start() < e for s, e in taken):
                hits.append(IbanHit(m.start(), m.end(), None, False, "ambiguous_iban"))
    return sorted(hits, key=lambda h: h.start)


def _person_name(text: str, start: int) -> tuple[int, int, str] | None:
    tokens = []
    pos = start
    end = start
    while len(tokens) < 4:
        m = NAME_TOKEN.match(text, pos)
        if not m:
            break
        token = m.group()
        word = token.rstrip(".")
        if word in NAME_STOP or not word:
            break
        initial = len(word) == 1 and token.endswith(".")
        if token.endswith(".") and not initial:
            tokens.append(word)
            end = m.start() + len(word)
            break
        tokens.append(token)
        end = m.end()
        if initial:
            break
        gap = re.match(r" +", text[m.end():])
        if not gap:
            break
        pos = m.end() + len(gap.group())
    if not tokens:
        return None
    return start, end, " ".join(tokens)


def find_holders(nt: NormalizedText) -> list[HolderHit]:
    text = nt.text
    names = {c.start: c for c in find_office_names(nt)}
    hits = []
    for m in HOLDER_LEAD.finditer(text):
        start = m.end()
        org = names.get(start)
        if org:
            hits.append(HolderHit(org.start, org.end, org.normalized, "organization", m.group("lead")))
            continue
        if m.group("lead") == "حساب" and not m.group("hon"):
            continue
        person = _person_name(text, start)
        if not person:
            continue
        s, e, value = person
        holder_type = "organization" if ORG_WORDS.search(value) else "person"
        hits.append(HolderHit(s, e, clean_spaces(value), holder_type, m.group("lead")))
    return hits


def _phrases(text: str, patterns, kind: str) -> list[PhraseHit]:
    spans = []
    for pattern in patterns:
        for m in pattern.finditer(text):
            spans.append((m.start(), m.end()))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s < merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1] = (merged[-1][0], e)
            continue
        merged.append((s, e))
    return [PhraseHit(s, e, kind) for s, e in merged]


def find_urgency(nt: NormalizedText) -> list[PhraseHit]:
    return _phrases(nt.text, URGENCY_PATTERNS, "urgency")


def find_payment_words(nt: NormalizedText) -> list[PhraseHit]:
    return _phrases(nt.text, [PAY_RE], "payment")


def find_account_mentions(nt: NormalizedText) -> list[PhraseHit]:
    return _phrases(nt.text, [ACCOUNT_RE], "account")


def find_new_account(nt: NormalizedText) -> list[PhraseHit]:
    return _phrases(nt.text, [CHANGE_RE, NEW_ACCOUNT_RE], "new_account")


def find_personal_account(nt: NormalizedText) -> list[PhraseHit]:
    return _phrases(nt.text, [PERSONAL_RE], "personal_account")
