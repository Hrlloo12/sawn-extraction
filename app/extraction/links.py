import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

from app.extraction.phones import parse_loose

TRAILING = ".,،؛;:!؟?)]}\"'»"
EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'،]+"
    r"|(?<![\w@.\-/])(?:wa\.me|t\.me|api\.whatsapp\.com|chat\.whatsapp\.com|instagram\.com|x\.com|twitter\.com"
    r"|snapchat\.com|tiktok\.com|facebook\.com|fb\.com)/[^\s<>\"'،]+"
    r"|(?<![\w@.\-/])[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.(?:com\.sa|com|net|org|sa|me|io|example|test)"
    r"(?:/[^\s<>\"'،]*)?(?![\w.])",
    re.IGNORECASE,
)
HANDLE_RE = re.compile(r"(?<![\w@.])@([A-Za-z0-9_][A-Za-z0-9_.]{1,29})")
LABELED_HANDLE_RE = re.compile(
    r"(سناب(?:\s*شات)?|snap\s*chat|snapchat|snap|انستا\w*|انستقرام|instagram|insta|تويتر|twitter"
    r"|تيك\s?توك|tiktok|تيليجرام|تلغرام|telegram)\s*(?:chat)?\s*[:：]\s*(?!@)([A-Za-z0-9_][A-Za-z0-9_.]{1,29})(?![:/\w@])",
    re.I,
)
HANDLE_CONTEXT = [
    ("snapchat", re.compile(r"سناب|snap", re.I)),
    ("instagram", re.compile(r"انستا|انستقرام|انستجرام|instagram|insta", re.I)),
    ("tiktok", re.compile(r"تيك ?توك|tiktok", re.I)),
    ("telegram", re.compile(r"تيليجرام|تلغرام|تليجرام|telegram", re.I)),
    ("facebook", re.compile(r"فيسبوك|فيس بوك|facebook", re.I)),
    ("x", re.compile(r"تويتر|twitter|منصة اكس|\bx\b|إكس", re.I)),
]
SOCIAL_HOSTS = {
    "instagram.com": "instagram",
    "x.com": "x",
    "twitter.com": "x",
    "snapchat.com": "snapchat",
    "tiktok.com": "tiktok",
    "t.me": "telegram",
    "telegram.me": "telegram",
    "facebook.com": "facebook",
    "fb.com": "facebook",
}
SOCIAL_NON_HANDLES = {"add", "p", "reel", "reels", "share", "intent", "home", "explore", "stories", "s", "joinchat"}
TRACKING_PARAMS = re.compile(r"^(utm_.*|fbclid|gclid|igshid|si|ref|ref_src|mc_cid|mc_eid)$", re.I)


@dataclass
class LinkContact:
    type: str
    normalized: str | None
    platform: str | None = None
    problem: str | None = None


def strip_trailing(value: str) -> str:
    value = value.strip()
    while value and value[-1] in TRAILING:
        if value[-1] == ")" and value.count("(") >= value.count(")"):
            break
        value = value[:-1]
    return value


def classify_url(raw: str) -> LinkContact | None:
    value = strip_trailing(raw)
    low = value.lower()
    if not value or low.startswith(("javascript:", "#", "data:")):
        return None
    if low.startswith("tel:"):
        parsed = parse_loose(unquote(value[4:]))
        if parsed:
            return LinkContact(parsed[1], parsed[0])
        return LinkContact("mobile", None, problem="invalid_phone")
    if low.startswith("mailto:"):
        email = unquote(value[7:].split("?")[0]).strip().lower()
        return LinkContact("email", email or None)
    if low.startswith(("whatsapp://", "sms:")):
        query = dict(parse_qsl(urlsplit(value).query))
        return _whatsapp_phone(query.get("phone", "")) if low.startswith("whatsapp") else None
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    path_parts = [unquote(p) for p in parts.path.split("/") if p]
    if host == "wa.me":
        if path_parts and re.fullmatch(r"\+?\d{6,15}", path_parts[0]):
            return _whatsapp_phone(path_parts[0])
        return LinkContact("whatsapp", None, problem="unresolved_link")
    if host in ("api.whatsapp.com", "web.whatsapp.com"):
        query = dict(parse_qsl(parts.query))
        return _whatsapp_phone(query.get("phone", ""))
    if host == "chat.whatsapp.com":
        return LinkContact("whatsapp", None, problem="whatsapp_group")
    if host in SOCIAL_HOSTS:
        platform = SOCIAL_HOSTS[host]
        handle = _social_handle(path_parts)
        if handle:
            return LinkContact("social", "@" + handle.lower(), platform)
        return LinkContact("social", normalize_website(value), platform)
    return LinkContact("website", normalize_website(value))


def _whatsapp_phone(value: str) -> LinkContact:
    digits = re.sub(r"\D", "", value)
    parsed = parse_loose("+" + digits) if digits and not digits.startswith("0") else parse_loose(digits)
    if parsed:
        return LinkContact("whatsapp", parsed[0])
    return LinkContact("whatsapp", None, problem="invalid_phone" if digits else "unresolved_link")


def _social_handle(path_parts: list[str]) -> str | None:
    parts = [p for p in path_parts if p.lower() not in SOCIAL_NON_HANDLES]
    if not parts:
        return None
    handle = parts[0].lstrip("@")
    return handle if re.fullmatch(r"[A-Za-z0-9_.]{2,30}", handle) else None


def normalize_website(value: str) -> str:
    parts = urlsplit(value if "://" in value else "https://" + value)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    result = host + path
    if query:
        result += "?" + urlencode(query)
    return result


def host_of(value: str) -> str:
    host = (urlsplit(value if "://" in value else "https://" + value).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def find_urls(text: str) -> list[tuple[int, int, str]]:
    found = []
    for m in URL_RE.finditer(text):
        value = strip_trailing(m.group())
        if value:
            found.append((m.start(), m.start() + len(value), value))
    return found


def find_emails(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in EMAIL_RE.finditer(text)]


def find_handles(text: str) -> list[tuple[int, int, str, str]]:
    found = []
    for m in HANDLE_RE.finditer(text):
        before = text[max(0, m.start() - 30) : m.start()]
        platform = "unknown"
        nearest = -1
        for name, pattern in HANDLE_CONTEXT:
            for hit in pattern.finditer(before):
                if hit.end() > nearest:
                    nearest = hit.end()
                    platform = name
        found.append((m.start(), m.end(), m.group(1), platform))
    for m in LABELED_HANDLE_RE.finditer(text):
        platform = next((name for name, pattern in HANDLE_CONTEXT if pattern.search(m.group(1))), "unknown")
        found.append((m.start(2), m.end(2), m.group(2).rstrip("."), platform))
    return sorted(found)
