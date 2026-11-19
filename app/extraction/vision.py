import base64
import re
from dataclasses import dataclass, field

import httpx

from app.config import Settings
from app.extraction import links as L
from app.extraction.normalize import normalize_text
from app.extraction.openai_client import LlmError, structured_response
from app.extraction.pipeline import MESSAGES, ExtractionOutput, compute_status
from app.schemas import Contact, Evidence, ExtractionWarning

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["domains"],
    "properties": {
        "domains": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["value", "legible"],
                "properties": {"value": {"type": "string"}, "legible": {"type": "boolean"}},
            },
        }
    },
}
INSTRUCTIONS = """You read a screenshot of an advertisement. List every website, domain or web link that appears in the image (not email addresses, not phone numbers, not social media handles).
Copy each one character by character exactly as it appears. Do not correct spelling, do not complete it and do not guess.
If any character of a link is not clearly legible, return it with legible=false and value="". Return an empty list when the image has no website."""
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
MIME = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"RIFF": "image/webp", b"GIF8": "image/gif"}


@dataclass
class VisionDomains:
    status: str
    values: list[str] = field(default_factory=list)
    illegible: int = 0
    error: str | None = None


def _data_url(data: bytes) -> str:
    mime = next((m for sig, m in MIME.items() if data.startswith(sig)), "image/png")
    return f"data:{mime};base64," + base64.b64encode(data).decode()


async def read_domains(data: bytes, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> VisionDomains:
    content = [{"type": "input_image", "image_url": _data_url(data), "detail": "high"}]
    try:
        answer = await structured_response(
            settings, settings.vision_model, INSTRUCTIONS, content, "ad_domains", SCHEMA, transport
        )
    except LlmError as error:
        status = "not_configured" if error.code == "llm_not_configured" else "failed"
        return VisionDomains(status=status, error=f"{error.code}: {error.message}")
    items = answer.get("domains", [])
    values = [i["value"].strip() for i in items if i.get("legible") and i.get("value", "").strip()]
    illegible = len(items) - len(values)
    if values:
        status = "read"
    elif illegible:
        status = "illegible"
    else:
        status = "none_found"
    return VisionDomains(status=status, values=values, illegible=illegible)


def valid_domain(normalized: str) -> bool:
    host = normalized.split("/", 1)[0].split("?", 1)[0]
    return bool(DOMAIN_RE.match(host))


def _warn(output: ExtractionOutput, code: str, field: str, detail: str = ""):
    message = MESSAGES[code] + (f": {detail}" if detail else "")
    output.warnings.append(ExtractionWarning(code=code, message=message, field=field))


def _website(raw: str, normalized: str | None, snippet: str, confidence: str) -> Contact:
    return Contact(
        type="website",
        raw_value=raw,
        normalized_value=normalized,
        source="ocr",
        evidence=Evidence(snippet=snippet),
        attribution="advertiser",
        confidence=confidence,
    )


def email_only(host: str, output: ExtractionOutput, text: str) -> bool:
    email_domains = {c.normalized_value.split("@", 1)[1] for c in output.contacts if c.type == "email" and c.normalized_value}
    if host not in email_domains:
        return False
    low = text.lower()
    return not any(low[i - 1 : i] != "@" for i in [m.start() for m in re.finditer(re.escape(host), low)])


def merge_domains(output: ExtractionOutput, vision: VisionDomains, text: str = "") -> ExtractionOutput:
    if vision.status == "read":
        for raw in vision.values:
            lc = L.classify_url(normalize_text(raw).text)
            if lc is None or lc.type != "website":
                continue
            if lc.normalized and email_only(lc.normalized.split("/", 1)[0], output, text):
                continue
            normalized = lc.normalized if lc.normalized and valid_domain(lc.normalized) else None
            output.contacts.append(_website(raw, normalized, raw, "high" if normalized else "low"))
            if normalized is None:
                _warn(output, "invalid_domain", f"contacts[{len(output.contacts) - 1}]", raw)
    if vision.illegible or vision.status in ("failed", "not_configured"):
        for withheld in output.withheld_websites:
            output.contacts.append(_website(withheld.raw_value, None, withheld.evidence.snippet, "low"))
            _warn(output, "domain_needs_confirmation", f"contacts[{len(output.contacts) - 1}]", withheld.raw_value)
        if vision.illegible and not output.withheld_websites:
            _warn(output, "domain_needs_confirmation", "contacts", "رابط غير واضح في الصورة")
    if vision.status in ("failed", "not_configured"):
        _warn(output, "domain_reader_unavailable", "contacts", vision.error or vision.status)
    output.withheld_websites = []
    output.status = compute_status(output.office_name, output.license_number, output.contacts, output.ibans)
    return output
