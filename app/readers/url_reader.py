import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from app.config import Settings
from app.readers.base import Line, Link, ReadError, ReadResult
from app.readers.http import shared_ssl_context

Resolver = Callable[[str, int], Awaitable[list[str]]]

BLOCKS = {
    "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "tr", "table",
    "section", "article", "header", "footer", "main", "aside", "nav", "body", "dd", "dt", "dl",
    "blockquote", "pre", "address", "form", "figure", "figcaption", "button", "label",
}
SKIP = {"script", "style", "noscript", "template", "title", "head", "svg", "iframe"}
HEADER_HINT = re.compile(r"(^|[-_ ])(header|navbar|nav|topbar|top-bar|masthead|site-header)($|[-_ ])", re.I)
FOOTER_HINT = re.compile(r"footer|copyright|bottom-bar", re.I)
LISTING_HINT = re.compile(r"(^|[-_ ])(listing|advert|advertiser|ad|ad-details|post|seller|classified|description)($|[-_ ])", re.I)
LOGIN_PATH = re.compile(r"/(login|signin|sign-in|auth|account/login)(/|$|\?)", re.I)
USER_AGENT = "SawnExtraction/1.0 (+demo)"


async def default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list({info[4][0] for info in infos})


def _domain_allowed(host: str, allowed: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in allowed)


async def check_url_allowed(url: str, settings: Settings, resolver: Resolver) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ReadError("invalid_url", "الرابط غير صالح، يجب أن يبدأ بـ http أو https", "paste_text")
    host = parts.hostname.lower().rstrip(".")
    if not _domain_allowed(host, settings.allowed_domain_list):
        raise ReadError("domain_not_allowed", "هذا النطاق غير مسموح في النسخة التجريبية", "upload_image", final_url=url)
    try:
        ips = await resolver(host, parts.port or (443 if parts.scheme == "https" else 80))
    except (OSError, socket.gaierror) as exc:
        raise ReadError("fetch_failed", "تعذّر الوصول إلى عنوان الصفحة", "upload_image", final_url=url) from exc
    if not ips:
        raise ReadError("fetch_failed", "تعذّر الوصول إلى عنوان الصفحة", "upload_image", final_url=url)
    for ip in ips:
        address = ipaddress.ip_address(ip.split("%")[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if address.is_loopback and settings.allow_localhost:
            continue
        if not address.is_global or address.is_multicast:
            raise ReadError("blocked_address", "الرابط يشير إلى عنوان داخلي غير مسموح", "upload_image", final_url=url)


async def fetch_page(
    url: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
) -> tuple[str, bytes, str | None]:
    resolver = resolver or default_resolver
    current = url
    try:
        async with asyncio.timeout(settings.fetch_timeout_seconds):
            async with httpx.AsyncClient(
                transport=transport,
                verify=shared_ssl_context(),
                timeout=settings.fetch_timeout_seconds,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            ) as client:
                for _ in range(settings.max_redirects + 1):
                    await check_url_allowed(current, settings, resolver)
                    async with client.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise ReadError("fetch_failed", "تحويل بدون وجهة", "upload_image", final_url=current)
                            current = urljoin(current, location)
                            continue
                        if response.status_code in (401, 403, 407):
                            raise ReadError("login_required", "الصفحة تطلب تسجيل دخول", "upload_image", final_url=current)
                        if response.status_code >= 400:
                            raise ReadError(
                                "fetch_failed", f"تعذّر فتح الصفحة (HTTP {response.status_code})", "upload_image", final_url=current
                            )
                        content_type = response.headers.get("content-type", "").lower()
                        if content_type and "html" not in content_type:
                            raise ReadError("unsupported_content", "الرابط لا يشير إلى صفحة ويب", "upload_image", final_url=current)
                        declared = response.headers.get("content-length")
                        if declared and declared.isdigit() and int(declared) > settings.max_response_bytes:
                            raise ReadError("response_too_large", "حجم الصفحة أكبر من الحد المسموح", "upload_image", final_url=current)
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > settings.max_response_bytes:
                                raise ReadError("response_too_large", "حجم الصفحة أكبر من الحد المسموح", "upload_image", final_url=current)
                        return current, bytes(body), response.charset_encoding
                raise ReadError("fetch_failed", "عدد التحويلات أكبر من المسموح", "upload_image", final_url=current)
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ReadError("timeout", "انتهت مهلة فتح الصفحة", "upload_image", final_url=current) from exc
    except httpx.HTTPError as exc:
        raise ReadError("fetch_failed", "تعذّر الاتصال بالصفحة", "upload_image", final_url=current) from exc


def _attrs_text(tag: Tag) -> str:
    classes = tag.get("class") or []
    return " ".join([*classes, tag.get("id") or "", tag.get("data-section") or ""])


def _inside_content(tag: Tag) -> bool:
    return tag.find_parent(["article", "main"]) is not None


def section_of(node, cache: dict) -> str:
    element = node if isinstance(node, Tag) else node.parent
    chain = []
    result = "unknown"
    current = element
    while isinstance(current, Tag):
        if id(current) in cache:
            result = cache[id(current)]
            break
        chain.append(current)
        role = (current.get("role") or "").lower()
        hints = _attrs_text(current)
        explicit = (current.get("data-section") or "").lower()
        if explicit in ("listing", "header", "footer"):
            result = explicit
            break
        if (current.name == "footer" and not _inside_content(current)) or role == "contentinfo" or FOOTER_HINT.search(hints):
            result = "footer"
            break
        if (
            (current.name in ("header", "nav") and not _inside_content(current))
            or role in ("banner", "navigation")
            or HEADER_HINT.search(hints)
        ):
            result = "header"
            break
        if current.name in ("main", "article") or role == "main" or LISTING_HINT.search(hints):
            result = "listing"
            break
        current = current.parent
    for tag in chain:
        cache[id(tag)] = result
    return result


def _is_hidden(tag: Tag) -> bool:
    style = (tag.get("style") or "").replace(" ", "").lower()
    return tag.has_attr("hidden") or "display:none" in style or "visibility:hidden" in style or tag.get("aria-hidden") == "true"


def parse_html(html: bytes | str, base_url: str, encoding: str | None = None) -> tuple[list[Line], list[Link], bool]:
    soup = BeautifulSoup(html, "html.parser", from_encoding=encoding if isinstance(html, bytes) else None)
    login = soup.find("input", attrs={"type": "password"}) is not None
    meta_lines: list[tuple[str, str]] = []
    if soup.title and soup.title.string:
        meta_lines.append(("page_title", soup.title.string))
    for attrs, source in (
        ({"property": "og:title"}, "page_title"),
        ({"name": "description"}, "page_meta"),
        ({"property": "og:description"}, "page_meta"),
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            meta_lines.append((source, tag["content"]))

    for tag in soup.find_all(SKIP):
        tag.decompose()
    for tag in soup.find_all(True):
        if not tag.decomposed and _is_hidden(tag):
            tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")

    cache: dict = {}
    blocks: list[tuple[int, str, list[str]]] = []
    for string in soup.find_all(string=True):
        if isinstance(string, Comment) or not isinstance(string, NavigableString):
            continue
        if not string.strip() and "\n" not in string:
            continue
        block = string.find_parent(lambda t: t.name in BLOCKS)
        key = id(block) if block is not None else 0
        if blocks and blocks[-1][0] == key:
            blocks[-1][2].append(str(string))
        else:
            blocks.append((key, section_of(string, cache), [str(string)]))

    lines: list[Line] = []
    seen_meta: set[str] = set()
    for _, section, parts in blocks:
        for piece in "".join(parts).split("\n"):
            text = re.sub(r"\s+", " ", piece).strip()
            if text:
                lines.append(Line(text=text, source="page_text", section=section, line_no=len(lines) + 1))
    for source, raw in meta_lines:
        text = re.sub(r"\s+", " ", raw).strip()
        if text and text not in seen_meta:
            seen_meta.add(text)
            lines.append(Line(text=text, source=source, section="listing", line_no=len(lines) + 1))

    links: list[Link] = []
    for tag in soup.find_all(True):
        href = tag.get("href") if tag.name in ("a", "area") else None
        href = href or tag.get("data-href") or tag.get("data-url")
        if not href:
            continue
        label = tag.get_text(" ", strip=True) or tag.get("aria-label") or tag.get("title") or ""
        links.append(
            Link(href=urljoin(base_url, href.strip()), text=re.sub(r"\s+", " ", label), section=section_of(tag, cache))
        )
    return lines, links, login


async def read_url(
    url: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
) -> ReadResult:
    url = url.strip()
    final_url, body, encoding = await fetch_page(url, settings, transport, resolver)
    lines, links, login = parse_html(body, final_url, encoding)
    if login or LOGIN_PATH.search(urlsplit(final_url).path + "/"):
        raise ReadError("login_required", "الصفحة تطلب تسجيل دخول", "upload_image", final_url=final_url)
    if sum(len(line.text) for line in lines if line.source == "page_text") < 20 and not links:
        raise ReadError("empty_page", "الصفحة لا تحتوي نصًا مقروءًا، قد تحتاج متصفحًا لعرضها", "upload_image", final_url=final_url)
    return ReadResult(input_type="url", lines=lines, links=links, input_url=url, final_url=final_url)
