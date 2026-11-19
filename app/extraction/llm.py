import re
from difflib import SequenceMatcher

import httpx

from app.config import Settings
from app.extraction import links as L
from app.extraction.normalize import clean_spaces, normalize_text
from app.extraction.office import GENERIC, _clean_name
from app.extraction.openai_client import LlmError, structured_response
from app.extraction.payment import ACCOUNT_RE, find_ibans, strip_marks
from app.extraction.phones import guess_type, parse_loose
from app.extraction.pipeline import MESSAGES, ExtractionOutput, compute_status
from app.readers.base import Line, ReadResult
from app.schemas import (
    AccountHolder,
    Contact,
    Evidence,
    ExtractionWarning,
    FieldValue,
    Iban,
    PaymentSignals,
    Phrase,
    Signal,
)

__all__ = ["LlmError", "extract_with_llm", "map_llm_output"]

ROLE = {"type": "string", "enum": ["advertiser", "platform", "unclear"]}
SOCIAL_PLATFORMS = ["instagram", "x", "snapchat", "tiktok", "telegram", "facebook", "unknown"]
HANDLE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.]{1,29}")
QUOTED_VALUE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value", "quote"],
    "properties": {"value": {"type": ["string", "null"]}, "quote": {"type": ["string", "null"]}},
}
FLAG = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value", "quotes"],
    "properties": {"value": {"type": "boolean"}, "quotes": {"type": "array", "items": {"type": "string"}}},
}


def _items(properties: dict) -> dict:
    return {
        "type": "array",
        "items": {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties},
    }


LLM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "office_name",
        "license_number",
        "phones",
        "urls",
        "emails",
        "social_handles",
        "ibans",
        "account_holder",
        "payment_request",
        "personal_account",
        "new_account_claim",
        "urgency_phrases",
    ],
    "properties": {
        "office_name": QUOTED_VALUE,
        "license_number": QUOTED_VALUE,
        "phones": _items(
            {
                "raw": {"type": "string"},
                "kind": {"type": "string", "enum": ["mobile", "landline", "toll_free", "whatsapp"]},
                "role": ROLE,
                "quote": {"type": "string"},
            }
        ),
        "urls": _items({"raw": {"type": "string"}, "role": ROLE, "quote": {"type": "string"}}),
        "emails": _items({"raw": {"type": "string"}, "role": ROLE, "quote": {"type": "string"}}),
        "social_handles": _items(
            {
                "raw": {"type": "string"},
                "platform": {"type": "string", "enum": SOCIAL_PLATFORMS},
                "role": ROLE,
                "quote": {"type": "string"},
            }
        ),
        "ibans": _items({"raw": {"type": "string"}, "quote": {"type": "string"}}),
        "account_holder": {
            "type": "object",
            "additionalProperties": False,
            "required": ["value", "holder_type", "quote"],
            "properties": {
                "value": {"type": ["string", "null"]},
                "holder_type": {"type": "string", "enum": ["organization", "person", "none"]},
                "quote": {"type": ["string", "null"]},
            },
        },
        "payment_request": FLAG,
        "personal_account": FLAG,
        "new_account_claim": FLAG,
        "urgency_phrases": {"type": "array", "items": {"type": "string"}},
    },
}

INSTRUCTIONS = """You extract facts from one Saudi domestic-worker recruitment ad (Arabic or English) for a fraud-check service.
Extract only what is written in the ad. Never infer, complete, correct or look up values. Copy every raw value and quote exactly as it appears, character for character.
Fields:
- office_name: the name of the office or company the ad claims to come from, as written, including words such as مكتب / شركة / للاستقدام / Recruitment, without branch names, slogans or descriptive words after the name such as الرسمي / المعتمد / المرخص. null if no name is written.
- license_number: only a number explicitly labelled as a licence (ترخيص / رخصة / License). Commercial registration is not a licence. null otherwise.
- phones: every phone or WhatsApp number. kind=whatsapp when the number is presented for WhatsApp, otherwise mobile, landline or toll_free (800 / 9200). If one number is given for both calls and WhatsApp, list it twice.
- urls: every website, domain or link as written (not emails). emails: every email address.
- social_handles: every social media account written as a handle (such as @name or "سناب: name"), with its platform when stated, otherwise unknown. Links to social sites go in urls.
- role for each channel: advertiser for the advertiser's own channels; unclear when it cannot be told whose channel it is, including support or customer-service numbers.
- ibans: every IBAN as written.
- account_holder: the name the payment account is held under, as written, without honorifics such as الأستاذ / السيد. holder_type=organization for an office, company or establishment, person for an individual, none if no holder is written (then value=null).
- payment_request: true when the ad asks the reader to pay or transfer money to a bank account or IBAN.
- personal_account: true when the payment account belongs to an individual person or the ad says it is a personal account.
- new_account_claim: true when the ad claims the bank account has changed or that there is a new account.
- urgency_phrases: the shortest exact phrases that pressure the reader to act fast: limited or special offers, limited counts, act now or today, before stock runs out, announcements that the account was changed. A payment, transfer or booking instruction is not urgency by itself unless it carries time pressure (today, now, before...). Do not include neutral descriptions, connecting words or delivery times.
- quote / quotes: the exact substring of the ad that supports the value.
Copy numbers, IBANs and links exactly as they appear, even if they look wrong or incomplete, and never add missing digits or letters.
If a field is absent use null, false or an empty list."""

ALIGN_MIN_RATIO = 0.85
TRAILING_GENERIC = GENERIC - {"و", "من", "في"}


def _compact(value: str) -> str:
    text = strip_marks(normalize_text(value or "")).text.lower()
    return re.sub(r"[\s\-–—_.،,:]+", "", text)


def strip_generic_tail(value: str) -> str:
    words = value.split()
    while len(words) > 2 and words[-1] in TRAILING_GENERIC:
        words.pop()
    return " ".join(words)


def align_to_source(value: str, lines: list[Line]) -> tuple[Line, str] | None:
    target = _compact(value)
    size = len(value.split())
    best = None
    for line in lines:
        words = line.text.split()
        for n in range(max(1, size - 1), size + 2):
            for i in range(0, max(0, len(words) - n) + 1):
                window = " ".join(words[i : i + n])
                ratio = SequenceMatcher(None, target, _compact(window)).ratio()
                if best is None or ratio > best[0]:
                    best = (ratio, line, window)
    if best and best[0] >= ALIGN_MIN_RATIO:
        return best[1], best[2]
    return None


class _LlmMapper:
    def __init__(self, read: ReadResult, data: dict):
        self.read = read
        self.data = data
        self.lines = [(line, _compact(line.text)) for line in read.lines]
        self.warnings: list[ExtractionWarning] = []

    def find(self, value: str | None) -> Line | None:
        key = _compact(value or "")
        if not key:
            return None
        return next((line for line, text in self.lines if key in text), None)

    def warn(self, code: str, field: str, detail: str = ""):
        message = MESSAGES.get(code, code) + (f": {detail}" if detail else "")
        self.warnings.append(ExtractionWarning(code=code, message=message, field=field))

    def evidence(self, line: Line | None, quote: str | None) -> Evidence:
        if line is not None:
            return Evidence(snippet=clean_spaces(line.text)[:200], line=line.line_no)
        return Evidence(snippet=clean_spaces(quote or "")[:200])

    def field_value(self, name: str, item: dict, upper: bool = False, is_name: bool = False) -> FieldValue | None:
        value = (item or {}).get("value")
        if not value:
            return None
        if is_name:
            value = strip_generic_tail(clean_spaces(value))
        line = self.find(value)
        if line is None and is_name:
            aligned = align_to_source(value, self.read.lines)
            if aligned:
                self.warn("llm_value_aligned_to_source", name, f"{value} ← {aligned[1]}")
                line, value = aligned[0], strip_generic_tail(aligned[1])
        line = line or self.find(item.get("quote"))
        confidence = "high"
        if line is None:
            self.warn("llm_value_not_in_source", name, value)
            confidence = "low"
        text = normalize_text(value).text
        normalized = _clean_name(text) if is_name else clean_spaces(text)
        return FieldValue(
            raw_value=value,
            normalized_value=normalized.upper() if upper else normalized,
            source="text",
            evidence=self.evidence(line, item.get("quote") or value),
            confidence=confidence,
        )

    def grounded(self, field: str, raw: str) -> Line | None:
        line = self.find(raw)
        if line is None:
            self.warn("llm_value_not_in_source", field, raw)
        return line

    def contact(self, ctype: str, platform: str | None, item: dict, normalized: str | None, line: Line) -> Contact:
        role = "advertiser" if item.get("role") == "advertiser" else "unclear"
        return Contact(
            type=ctype,
            platform=platform,
            raw_value=item["raw"],
            normalized_value=normalized,
            source="text",
            evidence=self.evidence(line, item.get("quote")),
            attribution=role,
            confidence="high" if normalized else "low",
        )

    def contacts(self) -> tuple[list[Contact], list[str | None]]:
        found: list[tuple[Contact, str | None]] = []
        for item in self.data.get("phones", []):
            line = self.grounded("phones", item["raw"])
            if line is None:
                continue
            parsed = parse_loose(normalize_text(item["raw"]).text)
            ctype = "whatsapp" if item.get("kind") == "whatsapp" else (parsed[1] if parsed else guess_type(re.sub(r"\D", "", item["raw"])))
            found.append((self.contact(ctype, None, item, parsed[0] if parsed else None, line), None if parsed else "invalid_phone"))
        for item in self.data.get("urls", []):
            line = self.grounded("urls", item["raw"])
            lc = L.classify_url(item["raw"]) if line else None
            if lc:
                found.append((self.contact(lc.type, lc.platform, item, lc.normalized, line), lc.problem))
        for item in self.data.get("emails", []):
            line = self.grounded("emails", item["raw"])
            if line:
                found.append((self.contact("email", None, item, item["raw"].strip().lower(), line), None))
        for item in self.data.get("social_handles", []):
            handle = item["raw"].strip().lstrip("@")
            line = self.grounded("social_handles", handle) if HANDLE_RE.fullmatch(handle) else None
            if line:
                found.append((self.contact("social", item.get("platform", "unknown"), item, "@" + handle.lower(), line), None))
        contacts, problems, seen = [], [], set()
        for contact, problem in found:
            key = (contact.type, contact.normalized_value or contact.raw_value)
            if key in seen:
                continue
            seen.add(key)
            contacts.append(contact)
            problems.append(problem)
        return contacts, problems

    def ibans(self) -> list[Iban]:
        result, seen = [], set()
        for item in self.data.get("ibans", []):
            line = self.grounded("ibans", item["raw"])
            if line is None:
                continue
            text = normalize_text(item["raw"]).text.strip()
            hit = next(iter(find_ibans(text)), None)
            normalized = hit.normalized if hit else None
            if (normalized or text) in seen:
                continue
            seen.add(normalized or text)
            problem = hit.problem if hit else "invalid_iban"
            if problem:
                self.warn(problem, f"ibans[{len(result)}]")
            result.append(
                Iban(
                    raw_value=item["raw"],
                    normalized_value=normalized,
                    checksum_valid=bool(hit and hit.checksum_valid),
                    source="text",
                    evidence=self.evidence(line, item.get("quote")),
                    confidence="high" if hit and normalized and hit.checksum_valid else "low",
                )
            )
        return result

    def holder(self, ibans: list[Iban]) -> AccountHolder | None:
        item = self.data.get("account_holder") or {}
        if not item.get("value") or item.get("holder_type") == "none":
            return None
        if not ibans and not any(ACCOUNT_RE.search(line.text) for line in self.read.lines):
            return None
        value = self.field_value("account_holder", item, is_name=item.get("holder_type") == "organization")
        return AccountHolder(**value.model_dump(), holder_type=item["holder_type"])

    def phrases(self, field: str, quotes: list[str]) -> list[Phrase]:
        result, seen = [], set()
        for quote in quotes:
            line = self.grounded(field, quote)
            text = clean_spaces(quote)
            if line is None or text in seen:
                continue
            seen.add(text)
            result.append(Phrase(text=text, source="text", evidence=self.evidence(line, quote)))
        return result

    def signal(self, name: str) -> Signal:
        item = self.data.get(name) or {}
        return Signal(value=bool(item.get("value")), evidence=self.phrases(name, item.get("quotes", [])))

    def run(self) -> ExtractionOutput:
        office = self.field_value("office_name", self.data.get("office_name"), is_name=True)
        if not office:
            self.warn("office_name_not_found", "office_name")
        license_number = self.field_value("license_number", self.data.get("license_number"), upper=True)
        contacts, problems = self.contacts()
        for i, (contact, problem) in enumerate(zip(contacts, problems)):
            if problem:
                self.warn(problem, f"contacts[{i}]")
            if contact.attribution == "unclear":
                self.warn("contact_attribution_unclear", f"contacts[{i}]")
        ibans = self.ibans()
        payment = PaymentSignals(
            payment_request=self.signal("payment_request"),
            personal_account=self.signal("personal_account"),
            new_account_claim=self.signal("new_account_claim"),
            urgency_phrases=self.phrases("urgency_phrases", self.data.get("urgency_phrases", [])),
        )
        return ExtractionOutput(
            status=compute_status(office, license_number, contacts, ibans),
            office_name=office,
            license_number=license_number,
            contacts=contacts,
            ibans=ibans,
            account_holder=self.holder(ibans),
            payment=payment,
            warnings=self.warnings,
        )


def map_llm_output(read: ReadResult, data: dict) -> ExtractionOutput:
    return _LlmMapper(read, data).run()


async def extract_with_llm(read: ReadResult, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> ExtractionOutput:
    text = "\n".join(line.text for line in read.lines)
    data = await structured_response(
        settings, settings.openai_model, INSTRUCTIONS, text, "sawn_ad_extraction", LLM_SCHEMA, transport
    )
    return map_llm_output(read, data)
