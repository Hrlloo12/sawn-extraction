from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.2"

Source = Literal["text", "ocr", "page_title", "page_meta", "page_text", "page_link"]
Section = Literal["listing", "header", "footer", "unknown"]
Confidence = Literal["high", "medium", "low"]
ContactType = Literal["mobile", "landline", "toll_free", "whatsapp", "email", "website", "social"]
Attribution = Literal["advertiser", "unclear", "platform"]
Status = Literal["ok", "partial", "not_found", "unreadable"]


class InputType(str, Enum):
    text = "text"
    url = "url"
    image = "image"


class Evidence(BaseModel):
    snippet: str = Field(description="السطر أو نص الزر الذي وُجدت فيه القيمة")
    section: Section | None = Field(None, description="قسم الصفحة لمسار الرابط")
    line: int | None = Field(None, description="رقم السطر في النص المقروء")
    bbox: list[float] | None = Field(None, description="مضلع موضع السطر في الصورة (x1,y1,...,x4,y4)")
    ocr_confidence: float | None = Field(None, description="أدنى ثقة OCR لحروف القيمة")
    href: str | None = Field(None, description="رابط الزر عند استخراج القيمة من رابط")


class FieldValue(BaseModel):
    raw_value: str
    normalized_value: str | None
    source: Source
    evidence: Evidence
    confidence: Confidence


class Contact(BaseModel):
    type: ContactType
    platform: str | None = Field(None, description="منصة الحساب عندما type=social")
    raw_value: str
    normalized_value: str | None = Field(description="E.164 للأرقام، null إذا كانت القراءة ملتبسة")
    source: Source
    evidence: Evidence
    attribution: Attribution
    confidence: Confidence


class Iban(BaseModel):
    raw_value: str
    normalized_value: str | None = Field(description="SA ثم 22 رقمًا بلا مسافات، null إذا كان الطول غير صحيح أو القراءة ملتبسة")
    checksum_valid: bool = Field(description="نتيجة فحص الآيبان (mod 97)")
    source: Source
    evidence: Evidence
    confidence: Confidence


class AccountHolder(FieldValue):
    holder_type: Literal["organization", "person"] = Field(description="organization إذا كان الاسم لمكتب أو شركة أو مؤسسة")


class Phrase(BaseModel):
    text: str = Field(description="العبارة كما وردت في الإعلان")
    source: Source
    evidence: Evidence


class Signal(BaseModel):
    value: bool = False
    evidence: list[Phrase] = Field(default_factory=list)


class PaymentSignals(BaseModel):
    payment_request: Signal = Field(default_factory=Signal, description="الإعلان يطلب تحويل مبلغ إلى حساب أو آيبان")
    personal_account: Signal = Field(default_factory=Signal, description="صاحب الحساب المذكور شخص وليس منشأة")
    new_account_claim: Signal = Field(default_factory=Signal, description="الإعلان يدّعي تغيير الحساب أو حسابًا جديدًا")
    urgency_phrases: list[Phrase] = Field(default_factory=list, description="عبارات استعجال أو ضغط")


class ExtractionWarning(BaseModel):
    code: str
    message: str
    field: str | None = None


class ErrorInfo(BaseModel):
    code: str
    message: str
    suggestion: Literal["upload_image", "paste_text"] | None = None


class Timing(BaseModel):
    read: int
    extract: int
    total: int


class ExtractorInfo(BaseModel):
    name: Literal["rules", "gpt"] = Field("rules", description="المحرك الذي استخرج الحقول فعليًا")
    model: str | None = Field(None, description="اسم نموذج OpenAI عند name=gpt")
    requested: Literal["rules", "gpt"] = Field("rules", description="المحرك المطلوب قبل أي رجوع للقواعد")
    fallback_reason: str | None = Field(None, description="سبب الرجوع للقواعد إذا تعذّر GPT")
    domain_reader: Literal["gpt_vision", "ocr"] | None = Field(None, description="قارئ الدومين في مسار الصورة")
    domain_status: Literal["read", "none_found", "illegible", "failed", "not_configured"] | None = Field(
        None, description="حالة قراءة الدومين بـGPT Vision في مسار الصورة"
    )
    vision_model: str | None = None


class ExtractResponse(BaseModel):
    schema_version: str = SCHEMA_VERSION
    request_id: str
    extractor: ExtractorInfo = Field(default_factory=ExtractorInfo)
    status: Status
    input_type: InputType
    input_url: str | None = None
    final_url: str | None = None
    office_name: FieldValue | None = None
    license_number: FieldValue | None = None
    contacts: list[Contact] = Field(default_factory=list)
    platform_contacts: list[Contact] = Field(default_factory=list)
    ibans: list[Iban] = Field(default_factory=list)
    account_holder: AccountHolder | None = None
    payment: PaymentSignals = Field(default_factory=PaymentSignals)
    warnings: list[ExtractionWarning] = Field(default_factory=list)
    error: ErrorInfo | None = None
    timing_ms: Timing


class PhoneCheckOffice(BaseModel):
    id: str
    name_ar: str
    name_en: str
    license_no: str
    city: str


class PhoneCheckReport(BaseModel):
    id: str
    claimed_office: PhoneCheckOffice | None
    count: int
    last_seen: str


class PhoneCheckResponse(BaseModel):
    input: str = Field(description="الرقم كما أُدخل")
    normalized_value: str = Field(description="E.164")
    phone_type: str = Field(description="mobile أو landline أو toll_free")
    result: Literal["registered", "unregistered", "reported"]
    registered: bool
    matcher_result: Literal["match", "no_match", "unregistered"] = Field(description="نتيجة matcher.py للقناة")
    verdict: str = Field(description="حكم matcher.py: يعود أو لا يعود أو لا نعرف")
    message: str
    office: PhoneCheckOffice | None = Field(None, description="المكتب المسجّل له الرقم، أو null")
    report: PhoneCheckReport | None = Field(None, description="بلاغ انتحال على الرقم إن وُجد")
    reference_data: str = "team_data (بيانات وهمية)"


class HealthResponse(BaseModel):
    status: str
    schema_version: str
    ocr_provider: str
    azure_configured: bool
    extractor: str = "rules"
    image_domain_reader: str = "ocr"
    openai_configured: bool = False
    openai_model: str | None = None
    vision_model: str | None = None
