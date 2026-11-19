import re
from dataclasses import dataclass

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberType

RUN_RE = re.compile(r"(?<![A-Za-z0-9@/=.+_#&?])(\+?\(?\d[\d?*]*\)?(?:[ .\-]{1,3}\(?[\d?*]+\)?)*)")
GROUP_RE = re.compile(r"[\d?*]+")
MARKERS = "?*"
MAX_DIGITS = 15
STRONG_PREFIXES = ("05", "9665", "009665", "966", "00966")

TYPE_MAP = {
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE: "landline",
    PhoneNumberType.TOLL_FREE: "toll_free",
    PhoneNumberType.SHARED_COST: "toll_free",
    PhoneNumberType.UAN: "toll_free",
}


@dataclass
class PhoneHit:
    start: int
    end: int
    digits: str
    e164: str | None
    kind: str
    base_type: str
    strong: bool = True


def parse_number(digits: str, plus: bool = False) -> tuple[str, str] | None:
    if not digits or any(m in digits for m in MARKERS):
        return None
    candidate = "+" + digits if plus else digits
    if candidate.startswith("00"):
        candidate = "+" + candidate[2:]
    elif candidate.startswith("966") and len(candidate) >= 11:
        candidate = "+" + candidate
    try:
        number = phonenumbers.parse(candidate, "SA")
    except NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    kind = TYPE_MAP.get(phonenumbers.number_type(number))
    if kind is None:
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164), kind


def parse_loose(value: str) -> tuple[str, str] | None:
    plus = value.strip().startswith("+")
    digits = re.sub(r"\D", "", value)
    return parse_number(digits, plus)


def expected_length(digits: str, plus: bool) -> int | None:
    if plus:
        return 12 if digits.startswith("966") else None
    if digits.startswith("00966"):
        return 14
    if digits.startswith("966"):
        return 12
    if digits.startswith("05"):
        return 10
    if re.match(r"01[1-7]", digits):
        return 10
    if digits.startswith("800"):
        return 10
    if digits.startswith("920"):
        return 9
    if digits.startswith("5"):
        return 9
    return None


def guess_type(digits: str) -> str:
    local = re.sub(r"^(00966|966)", "0", digits)
    if local.startswith(("05", "5")):
        return "mobile"
    if local.startswith("01"):
        return "landline"
    if local.startswith(("800", "920", "0800", "0920")):
        return "toll_free"
    return "mobile"


def find_phones(text: str) -> list[PhoneHit]:
    hits: list[PhoneHit] = []
    for match in RUN_RE.finditer(text):
        run_start = match.start(1)
        run = match.group(1)
        plus_run = run.startswith("+")
        groups = [(run_start + g.start(), run_start + g.end(), g.group()) for g in GROUP_RE.finditer(run)]
        i = 0
        while i < len(groups):
            plus = plus_run and i == 0
            start = groups[i][0] - (1 if plus else 0)
            best = None
            acc = ""
            for j in range(i, len(groups)):
                acc += groups[j][2]
                if len(acc) > MAX_DIGITS:
                    break
                parsed = parse_number(acc, plus)
                if parsed:
                    best = (j, acc, parsed)
            if best:
                j, acc, (e164, kind) = best
                hits.append(PhoneHit(start, groups[j][1], acc, e164, "valid", kind))
                i = j + 1
                continue
            partial = _partial_hit(groups, i, plus, start)
            if partial:
                hit, next_i = partial
                hits.append(hit)
                i = next_i
                continue
            i += 1
    return hits


def _partial_hit(groups, i, plus, start):
    first = groups[i][2]
    exp = expected_length(first.replace("?", "0").replace("*", "0"), plus)
    if exp is None:
        return None
    acc = ""
    j = i
    while j < len(groups) and len(acc) + len(groups[j][2]) <= exp:
        acc += groups[j][2]
        j += 1
    if j == i:
        if len(first) <= MAX_DIGITS:
            acc, j = first, i + 1
        else:
            return None
    if len(acc) < max(7, exp - 3):
        return None
    has_marker = any(m in acc for m in MARKERS)
    if not has_marker and len(acc) == exp and parse_number(acc, plus):
        return None
    strong = plus or acc.startswith(STRONG_PREFIXES)
    kind = "ambiguous" if has_marker else "invalid"
    return PhoneHit(start, groups[j - 1][1], acc, None, kind, guess_type(acc), strong), j
