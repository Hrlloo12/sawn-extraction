import re
import unicodedata
from dataclasses import dataclass

from app.extraction.normalize import NormalizedText, clean_spaces

PREFIX = r"(?:مكتب|شركة|شركه|مؤسسة|مؤسسه|مركز)"
DOMAIN_TERMS = sorted(
    [
        "للاستقدام",
        "الاستقدام",
        "استقدام",
        "لاستقدام",
        "للتوظيف",
        "التوظيف",
        "توظيف",
        "للعمالة المنزلية",
        "العمالة المنزلية",
        "للعمالة",
        "العمالة",
        "للموارد البشرية",
        "الموارد البشرية",
        "لتأجير العمالة",
        "للخدمات العمالية",
    ],
    key=len,
    reverse=True,
)
DOMAIN = "(?:" + "|".join(DOMAIN_TERMS) + ")"
WORD = r"[^\s\d:：|،؛,.!؟?()\[\]\-–/\\\"'«»@]+"
BOUNDARY = r"(?=$|[\s،؛,.:：|!؟?)\-–])"
GENERIC = {
    "معتمد",
    "المعتمد",
    "مرخص",
    "المرخص",
    "رسمي",
    "الرسمي",
    "موثوق",
    "خدمات",
    "لخدمات",
    "للخدمات",
    "افضل",
    "أفضل",
    "نحن",
    "و",
    "من",
    "في",
    "متخصص",
    "مختص",
}
TAIL_STOP = {
    "في",
    "من",
    "مع",
    "على",
    "الى",
    "إلى",
    "لدينا",
    "نوفر",
    "يوفر",
    "نقدم",
    "يقدم",
    "معتمد",
    "مرخص",
    "رسمي",
    "متوفر",
    "يعلن",
    "تعلن",
    "عن",
}

PREFIX_ONLY_STOP = TAIL_STOP | {
    "يرحب",
    "ترحب",
    "تبحث",
    "يبحث",
    "توفر",
    "تقدم",
    "عندنا",
    "لديه",
    "لديها",
    "فرع",
    "للحجز",
    "للتواصل",
    "تواصل",
    "اتصل",
    "يسر",
    "تسر",
    "هو",
    "هي",
    "الذي",
    "التي",
    "رقم",
    "عن",
    "عبر",
    "على",
    "باسم",
}
PREFIX_ONLY_MAX_WORDS = 4
PATTERN_PREFIX_ONLY = re.compile(rf"(?<!\S){PREFIX}((?:\s+{WORD}){{1,{PREFIX_ONLY_MAX_WORDS}}})")
DOMAIN_COMPLEMENT =r"(?:\s+(?:العمالة|العمال|الأيدي العاملة|الايدي العاملة)(?:\s+المنزلية)?)?"
PATTERN_NAME_FIRST = re.compile(rf"{PREFIX}((?:\s+{WORD}){{0,6}}?)\s+{DOMAIN}{DOMAIN_COMPLEMENT}{BOUNDARY}")
PATTERN_DOMAIN_FIRST = re.compile(rf"{PREFIX}\s+{DOMAIN}((?:\s+{WORD}){{1,2}}){BOUNDARY}")
LABELED = re.compile(
    r"^\s*(?:اسم المعلن|المعلن|اسم المكتب|اسم الشركة|اسم المنشأة|الجهة المعلنة"
    r"|advertiser|office name|company name|agency name)\s*[:：\-–]\s*(?P<v>.+)$",
    re.IGNORECASE,
)
ENGLISH = re.compile(
    r"\b((?:[A-Z][A-Za-z&'.]*\s+){1,5}(?:Recruitment|Recruiting|Manpower|Staffing)"
    r"(?:\s+(?:Office|Company|Co\.?|Agency|Services|Est\.?|Establishment))?)(?!\w)"
)
ENGLISH_GENERIC = {"the", "licensed", "official", "contact", "best", "trusted", "a", "an", "our", "your"}
LICENSE_RE = re.compile(
    r"(?:الترخيص|ترخيص|الرخصة|رخصة|license|licence)"
    r"(?:\s+(?!\d|جوال|واتس|هاتف|اتصال|للتواصل|تواصل|موبايل|whats|phone|mobile|call|tel)[^\s\d:：#]+){0,4}?"
    r"\s*(?:رقم|no\.?|number|num\.?|#)?\s*[:：#\-]?\s*"
    r"(?P<num>(?:[A-Za-z]{1,3}[\-/]?)?\d[\d\-/]{2,18}\d)(?!\d)",
    re.IGNORECASE,
)


@dataclass
class NameCandidate:
    start: int
    end: int
    normalized: str
    method: str
    priority: int


@dataclass
class LicenseHit:
    start: int
    end: int
    normalized: str


def _words(text: str) -> list[str]:
    return [w for w in text.split() if w]


def _clean_name(value: str) -> str:
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return clean_spaces(value).strip(" -–|:،؛,.")


def find_office_names(nt: NormalizedText) -> list[NameCandidate]:
    text = nt.text
    found: list[NameCandidate] = []
    labeled = LABELED.match(text)
    if labeled:
        value = labeled.group("v")
        cut = re.search(r"\s[|\-–]\s|،|\d{3,}|https?://|@", value)
        if cut:
            value = value[: cut.start()]
        value = _clean_name(value)
        if 3 <= len(value) <= 80:
            start = labeled.start("v")
            found.append(NameCandidate(start, start + len(value), value, "labeled", 3))
    for m in PATTERN_NAME_FIRST.finditer(text):
        middle = _words(m.group(1))
        if any(w not in GENERIC for w in middle):
            found.append(NameCandidate(m.start(), m.end(), _clean_name(m.group()), "pattern", 2))
    for m in PATTERN_DOMAIN_FIRST.finditer(text):
        tail = []
        for w in _words(m.group(1)):
            if w in TAIL_STOP or w.startswith("بال"):
                break
            tail.append(w)
        if tail and not any(c.start <= m.start() < c.end for c in found):
            prefix_len = m.start(1) - m.start()
            name = text[m.start() : m.start() + prefix_len] + " " + " ".join(tail)
            end = m.start(1) + text[m.start(1) :].find(tail[-1]) + len(tail[-1])
            found.append(NameCandidate(m.start(), end, _clean_name(name), "pattern_domain_first", 1))
    for m in PATTERN_PREFIX_ONLY.finditer(text):
        if any(c.start <= m.start() < c.end or m.start() <= c.start < m.end() for c in found):
            continue
        tail = []
        for w in _words(m.group(1)):
            if w in PREFIX_ONLY_STOP or w.startswith("بال"):
                break
            tail.append(w)
        if not tail or all(w in GENERIC or w in DOMAIN_TERMS for w in tail):
            continue
        end = m.start(1) + text[m.start(1) :].find(tail[-1]) + len(tail[-1])
        prefix_len = m.start(1) - m.start()
        name = text[m.start() : m.start() + prefix_len] + " " + " ".join(tail)
        found.append(NameCandidate(m.start(), end, _clean_name(name), "prefix_only", 0))
    for m in ENGLISH.finditer(text):
        words = m.group(1).split()
        while words and words[0].lower() in ENGLISH_GENERIC:
            words = words[1:]
        if len(words) >= 2:
            name = " ".join(words)
            start = m.start(1) + m.group(1).find(words[0])
            found.append(NameCandidate(start, start + len(name), name, "english", 2))
    return found


def name_key(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("ة", "ه").replace("أ", "ا").replace("إ", "ا").lower()


def find_licenses(nt: NormalizedText) -> list[LicenseHit]:
    hits = []
    for m in LICENSE_RE.finditer(nt.text):
        hits.append(LicenseHit(m.start("num"), m.end("num"), m.group("num").upper()))
    return hits
