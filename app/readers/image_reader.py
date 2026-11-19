import asyncio
import hashlib
import json
from pathlib import Path

import httpx

from app.config import Settings
from app.readers.base import Line, ReadError, ReadResult
from app.readers.http import shared_ssl_context
from app.readers.tesseract_reader import TesseractProvider

SIGNATURES = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
]


def sniff_image(data: bytes) -> str | None:
    for signature, mime in SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"msf1"):
        return "image/heif"
    return None


class AzureReadProvider:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def analyze(self, data: bytes) -> dict:
        s = self.settings
        if not s.azure_configured:
            raise ReadError("ocr_not_configured", "قراءة الصور عبر Azure غير مفعّلة في هذه البيئة", "paste_text")
        url = (
            f"{s.azure_di_endpoint.rstrip('/')}/documentintelligence/documentModels/prebuilt-read:analyze"
            f"?api-version={s.azure_di_api_version}"
        )
        headers = {"Ocp-Apim-Subscription-Key": s.azure_di_key}
        try:
            async with asyncio.timeout(s.ocr_timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self.transport, verify=shared_ssl_context(), timeout=s.ocr_timeout_seconds
                ) as client:
                    response = await client.post(
                        url, content=data, headers=headers | {"Content-Type": "application/octet-stream"}
                    )
                    if response.status_code != 202 or "operation-location" not in response.headers:
                        raise ReadError("ocr_failed", f"رفضت خدمة OCR الطلب (HTTP {response.status_code})", "paste_text")
                    operation = response.headers["operation-location"]
                    delay = 0.25
                    while True:
                        poll = await client.get(operation, headers=headers)
                        body = poll.json()
                        status = body.get("status")
                        if status == "succeeded":
                            return body.get("analyzeResult", {})
                        if status == "failed":
                            raise ReadError("ocr_failed", "تعذّرت قراءة الصورة في خدمة OCR", "paste_text")
                        await asyncio.sleep(delay)
                        delay = min(delay * 1.5, 1.0)
        except TimeoutError as exc:
            raise ReadError("timeout", "انتهت مهلة قراءة الصورة", "paste_text") from exc
        except httpx.HTTPError as exc:
            raise ReadError("ocr_failed", "تعذّر الاتصال بخدمة OCR", "paste_text") from exc


class FixtureOcrProvider:
    def __init__(self, directory: Path):
        self.index: dict[str, Path] = {}
        if directory.exists():
            for path in directory.glob("*.json"):
                sha = json.loads(path.read_text(encoding="utf-8")).get("sha256")
                if sha:
                    self.index[sha] = path

    async def analyze(self, data: bytes) -> dict:
        path = self.index.get(hashlib.sha256(data).hexdigest())
        if path is None:
            raise ReadError(
                "ocr_not_configured",
                "وضع العينات المحفوظة مفعّل ولا توجد قراءة محفوظة لهذه الصورة؛ قراءة صورة جديدة تحتاج مفتاح Azure",
                "paste_text",
            )
        return json.loads(path.read_text(encoding="utf-8"))["analyzeResult"]


def get_ocr_provider(settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
    if settings.ocr_provider == "fixture":
        return FixtureOcrProvider(settings.ocr_fixtures_path)
    if settings.ocr_provider == "tesseract":
        return TesseractProvider(settings)
    return AzureReadProvider(settings, transport)


def lines_from_analyze_result(result: dict) -> list[Line]:
    content = result.get("content", "")
    char_conf: list[float | None] = [None] * len(content)
    for page in result.get("pages", []):
        for word in page.get("words", []):
            span = word.get("span") or {}
            offset, length = span.get("offset"), span.get("length", 0)
            if offset is None:
                continue
            for k in range(offset, min(offset + length, len(content))):
                char_conf[k] = word.get("confidence")
    lines: list[Line] = []
    for page in result.get("pages", []):
        for raw in page.get("lines", []):
            text = raw.get("content", "")
            if not text.strip():
                continue
            offsets = [o for span in raw.get("spans", []) for o in range(span["offset"], span["offset"] + span["length"])]
            confidences = None
            if len(offsets) == len(text) and offsets and offsets[-1] < len(content):
                values = [char_conf[o] for o in offsets]
                if any(v is not None for v in values):
                    confidences = [v if v is not None else 1.0 for v in values]
            line_conf = min((c for c, ch in zip(confidences, text) if not ch.isspace()), default=None) if confidences else None
            lines.append(
                Line(
                    text=text,
                    source="ocr",
                    line_no=len(lines) + 1,
                    bbox=raw.get("polygon"),
                    confidence=line_conf,
                    char_confidence=confidences,
                )
            )
    return lines


def validate_image(data: bytes, settings: Settings):
    if len(data) > settings.max_image_bytes:
        raise ReadError("file_too_large", "حجم الصورة أكبر من الحد المسموح", "paste_text")
    if not data or sniff_image(data) is None:
        raise ReadError("unsupported_file", "الملف ليس صورة مدعومة (JPEG أو PNG أو WEBP أو HEIF أو BMP أو TIFF)", "paste_text")


async def read_image(data: bytes, settings: Settings, provider=None) -> ReadResult:
    validate_image(data, settings)
    provider = provider or get_ocr_provider(settings)
    result = await provider.analyze(data)
    return ReadResult(input_type="image", lines=lines_from_analyze_result(result))
