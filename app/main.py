import asyncio
import json
import time
import uuid
from dataclasses import dataclass

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.config import PROJECT_ROOT, Settings, get_settings
from app.extraction.llm import LlmError, extract_with_llm
from app.extraction.pipeline import MESSAGES, extract
from app.extraction.vision import merge_domains, read_domains
from app.phone_check import InvalidPhone, check_phone
from app.readers.base import ReadError
from app.readers.image_reader import read_image, validate_image
from app.readers.text_reader import read_text
from app.readers.url_reader import Resolver, read_url
from app.schemas import SCHEMA_VERSION, ErrorInfo, ExtractionWarning, ExtractorInfo, ExtractResponse, HealthResponse, PhoneCheckResponse, InputType, Timing

EXAMPLES_DIR = PROJECT_ROOT / "docs" / "examples"
REQUEST_SCHEMA = {
    "type": "object",
    "required": ["input_type"],
    "properties": {
        "input_type": {"type": "string", "enum": ["text", "url", "image"], "description": "نوع المدخل"},
        "value": {"type": "string", "description": "نص الإعلان (text) أو رابطه (url)"},
        "file": {"type": "string", "format": "binary", "description": "صورة الإعلان (image)"},
        "extractor": {"type": "string", "enum": ["rules", "gpt"], "description": "لمسار النص فقط: محرك الاستخراج، والافتراضي من EXTRACTOR (gpt)"},
    },
}

DESCRIPTION = """
خدمة استخراج «صون»: تقرأ إعلان الاستقدام (نص، صورة، رابط) وترجع اسم المكتب ورقم الترخيص
وقنوات التواصل كما وردت، مع الصيغة الموحّدة ومصدر كل قيمة ودليلها.

الخدمة لا تقرر نتيجة المطابقة؛ قرار «تعود للجهة / لا تعود / غير كافية» في خدمة المطابقة.

- `status`: `ok` | `partial` | `not_found` | `unreadable`
- `contacts`: قنوات المعلن أو القنوات غير محسومة النسبة (`attribution: unclear`)
- `platform_contacts`: أرقام وحسابات المنصة (رأس الصفحة، التذييل، الدعم الفني) مفصولة عن قنوات المعلن
- `ibans` و`account_holder`: الحسابات البنكية وصاحب الحساب كما وردت في الإعلان
- `payment`: مؤشرات طلب الدفع والحساب الشخصي وتغيير الحساب وعبارات الاستعجال، مع العبارة التي دلّت على كل مؤشر
"""


@dataclass
class ReaderDeps:
    http_transport: httpx.AsyncBaseTransport | None = None
    resolver: Resolver | None = None
    ocr_provider: object | None = None
    llm_transport: httpx.AsyncBaseTransport | None = None


def get_reader_deps() -> ReaderDeps:
    return ReaderDeps()


def load_examples() -> dict:
    examples = {}
    for path in sorted(EXAMPLES_DIR.glob("response_*.json")):
        name = path.stem.removeprefix("response_")
        examples[name] = {"summary": name, "value": json.loads(path.read_text(encoding="utf-8"))}
    return examples


settings_at_start = get_settings()
app = FastAPI(title="Sawn Extraction API", version=SCHEMA_VERSION, description=DESCRIPTION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings_at_start.cors_origin_list,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def _ms(start: float, end: float) -> int:
    return round((end - start) * 1000)


def _bad_request(code: str, message: str):
    raise HTTPException(status_code=422, detail={"code": code, "message": message})


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/docs")


@app.get("/health", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)):
    return HealthResponse(
        status="ok",
        schema_version=SCHEMA_VERSION,
        ocr_provider=settings.ocr_provider,
        azure_configured=settings.azure_configured,
        extractor=settings.extractor,
        image_domain_reader=settings.image_domain_reader,
        openai_configured=settings.openai_configured,
        openai_model=settings.openai_model,
        vision_model=settings.vision_model,
    )


@app.post(
    "/extract",
    response_model=ExtractResponse,
    summary="استخراج اسم المكتب وقنوات التواصل من إعلان",
    responses={
        200: {"content": {"application/json": {"examples": load_examples()}}},
        422: {"description": "طلب غير صالح، مثل رابط بدون value أو صورة بدون file"},
    },
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": REQUEST_SCHEMA}}}},
)
async def extract_endpoint(
    request: Request,
    settings: Settings = Depends(get_settings),
    deps: ReaderDeps = Depends(get_reader_deps),
):
    request_id = uuid.uuid4().hex
    started = time.perf_counter()
    try:
        form = await request.form()
    except Exception:
        _bad_request("invalid_form", "أرسل الطلب بصيغة multipart/form-data")
    try:
        input_type = InputType(str(form.get("input_type", "")).strip())
    except ValueError:
        _bad_request("invalid_input_type", "input_type يجب أن يكون text أو url أو image")
    raw_value = form.get("value")
    value = raw_value.strip() if isinstance(raw_value, str) else None
    file = form.get("file")
    upload = file if isinstance(file, StarletteUploadFile) and file.filename is not None else None
    input_url = None
    requested = str(form.get("extractor") or settings.extractor).strip().lower()
    if requested not in ("rules", "gpt"):
        _bad_request("invalid_extractor", "extractor يجب أن يكون rules أو gpt")
    if input_type != InputType.text:
        requested = "rules"
    extractor_info = ExtractorInfo(name="rules", requested=requested)

    if input_type == InputType.text:
        if not value:
            _bad_request("missing_value", "أرسل نص الإعلان في الحقل value")
        if len(value) > settings.max_text_chars:
            _bad_request("text_too_long", f"النص أطول من {settings.max_text_chars} حرف")
    elif input_type == InputType.url:
        if not value:
            _bad_request("missing_value", "أرسل رابط الإعلان في الحقل value")
        input_url = value
    elif upload is None:
        _bad_request("missing_file", "أرسل صورة الإعلان في الحقل file")

    vision_task = None
    try:
        if input_type == InputType.text:
            read = read_text(value)
        elif input_type == InputType.url:
            read = await read_url(value, settings, deps.http_transport, deps.resolver)
        else:
            data = await upload.read(settings.max_image_bytes + 1)
            validate_image(data, settings)
            if settings.image_domain_reader == "vision":
                vision_task = asyncio.create_task(read_domains(data, settings, deps.llm_transport))
            read = await read_image(data, settings, deps.ocr_provider)
    except ReadError as error:
        if vision_task:
            vision_task.cancel()
        elapsed = _ms(started, time.perf_counter())
        return ExtractResponse(
            request_id=request_id,
            extractor=extractor_info,
            status="unreadable",
            input_type=input_type,
            input_url=input_url,
            final_url=error.final_url,
            error=ErrorInfo(code=error.code, message=error.message, suggestion=error.suggestion),
            timing_ms=Timing(read=elapsed, extract=0, total=elapsed),
        )

    read_done = time.perf_counter()
    output = None
    if requested == "gpt":
        try:
            output = await extract_with_llm(read, settings, deps.llm_transport)
            extractor_info.name, extractor_info.model = "gpt", settings.openai_model
        except LlmError as error:
            extractor_info.fallback_reason = f"{error.code}: {error.message}"
    if output is None:
        output = extract(read, settings.ocr_min_digit_confidence, withhold_websites=vision_task is not None)
    if extractor_info.fallback_reason:
        output.warnings.append(
            ExtractionWarning(code="llm_fallback_rules", message=MESSAGES["llm_fallback_rules"], field=None)
        )
    if input_type == InputType.image:
        extractor_info.domain_reader = "gpt_vision" if vision_task else "ocr"
        if vision_task:
            vision = await vision_task
            output = merge_domains(output, vision, "\n".join(line.text for line in read.lines))
            extractor_info.domain_status = vision.status
            extractor_info.vision_model = settings.vision_model
    done = time.perf_counter()
    return ExtractResponse(
        request_id=request_id,
        extractor=extractor_info,
        status=output.status,
        input_type=input_type,
        input_url=read.input_url,
        final_url=read.final_url,
        office_name=output.office_name,
        license_number=output.license_number,
        contacts=output.contacts,
        platform_contacts=output.platform_contacts,
        ibans=output.ibans,
        account_holder=output.account_holder,
        payment=output.payment,
        warnings=output.warnings,
        timing_ms=Timing(read=_ms(started, read_done), extract=_ms(read_done, done), total=_ms(started, done)),
    )


@app.post(
    "/check/phone",
    response_model=PhoneCheckResponse,
    summary="فحص رقم جوال أو هاتف وحده مقابل القاعدة التجريبية",
    description="يوحّد الرقم ثم يمرره إلى team_data/matcher.py مع entities.json وreports.json، بدون اسم مكتب أو نص إعلان.",
    responses={422: {"description": "invalid_phone أو missing_value"}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["phone"],
                        "properties": {"phone": {"type": "string", "description": "مثل 0500000011 أو +966500000011"}},
                    }
                }
            },
        }
    },
)
async def check_phone_endpoint(request: Request, settings: Settings = Depends(get_settings)):
    try:
        form = await request.form()
    except Exception:
        _bad_request("invalid_form", "أرسل الطلب بصيغة multipart/form-data")
    phone = str(form.get("phone") or "").strip()
    if not phone:
        _bad_request("missing_value", "أرسل الرقم في الحقل phone")
    try:
        return check_phone(phone, settings)
    except InvalidPhone:
        _bad_request("invalid_phone", "الرقم ليس بصيغة سعودية صالحة")
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail={"code": "reference_data_missing", "message": "القاعدة التجريبية غير موجودة في REFERENCE_DATA_DIR"},
        )
