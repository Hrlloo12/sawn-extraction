import re
from dataclasses import dataclass, field

from app.extraction import links as L
from app.extraction.normalize import NormalizedText, clean_spaces, mask, normalize_text
from app.extraction import payment as P
from app.extraction.office import find_licenses, find_office_names, name_key
from app.extraction.phones import find_phones
from app.readers.base import Line, Link, ReadResult
from app.schemas import AccountHolder, Contact, Evidence, ExtractionWarning, FieldValue, Iban, PaymentSignals, Phrase, Signal

WHATSAPP_KW = re.compile(r"وات?س|whats\s?app|\bwa\b", re.I)
CALL_KW = re.compile(r"جوال|جوّال|موبايل|اتصال|اتصل|هاتف|تلفون|تليفون|mobile|phone|\btel\b|call|cell", re.I)
CONTACT_KW = re.compile(r"للتواصل|تواصل|رقم|contact", re.I)
AD_ID_KW = re.compile(
    r"رقم الإعلان|رقم الاعلان|كود الإعلان|كود الاعلان|رقم المرجع|سجل تجاري|السجل التجاري|\bad\s*(id|no)|\bref\b|\bcr\b",
    re.I,
)
SUPPORT_KW = re.compile(
    r"دعم|خدمة العملاء|خدمه العملاء|لخدمة العملاء|مركز المساعدة|الإبلاغ|الابلاغ|support|customer\s*(care|service)|help\s*(center|desk)",
    re.I,
)
LETTER = re.compile(r"[^\W\d_]")
SEPARATORS_ONLY = re.compile(r"^[\s\-/،,|و&]*$")
SNIPPET_MAX = 200
LOW_OCR = 0.8

MESSAGES = {
    "office_name_not_found": "لم يُعثر على اسم مكتب واضح في المدخل",
    "multiple_office_names": "ظهر أكثر من اسم مكتب، أُرجع الأوضح منها",
    "multiple_license_numbers": "ظهر أكثر من رقم ترخيص، أُرجع الأول",
    "ambiguous_phone": "قراءة الرقم ناقصة أو ملتبسة، لم يُوحَّد",
    "invalid_phone": "الرقم لا يطابق صيغة رقم صالحة، لم يُوحَّد",
    "unresolved_link": "رابط التواصل لا يحتوي رقمًا يمكن استخراجه",
    "whatsapp_group": "رابط مجموعة واتساب وليس رقمًا",
    "contact_attribution_unclear": "تعذّر التأكد أن هذه القناة تعود للمعلن",
    "low_ocr_confidence": "ثقة قراءة الصورة منخفضة لهذه القيمة",
    "ambiguous_link": "قراءة الرابط أو الحساب ملتبسة، لم يُوحَّد",
    "invalid_iban": "رقم آيبان بطول غير صحيح، لم يُوحَّد",
    "iban_checksum_invalid": "الآيبان لا يجتاز فحص الصحة (mod 97)، قد يكون فيه خطأ",
    "ambiguous_iban": "قراءة الآيبان ملتبسة، لم يُوحَّد",
    "multiple_account_holders": "ظهر أكثر من صاحب حساب، أُرجع الأول",
    "llm_fallback_rules": "تعذّر طلب GPT، فاستُخدمت قواعد الاستخراج",
    "invalid_domain": "الدومين المقروء من الصورة ليس بصيغة صالحة، لم يُوحَّد",
    "domain_needs_confirmation": "الدومين في الصورة غير مؤكد القراءة، اطلب من المستخدم لصق الرابط أو تأكيده",
    "domain_reader_unavailable": "تعذّرت قراءة الدومين من الصورة عبر GPT Vision",
    "llm_value_aligned_to_source": "اسم من النموذج اللغوي لم يرد حرفيًا، فاستُبدل بأقرب نص مطابق في الإعلان",
    "llm_value_not_in_source": "قيمة من النموذج اللغوي لم تُوجد في نص الإعلان، استُبعدت أو خُفّضت ثقتها",
}


@dataclass
class Candidate:
    contact: Contact
    problem: str | None = None
    position: int = 0


@dataclass
class ExtractionOutput:
    status: str
    office_name: FieldValue | None
    license_number: FieldValue | None
    contacts: list[Contact] = field(default_factory=list)
    platform_contacts: list[Contact] = field(default_factory=list)
    ibans: list[Iban] = field(default_factory=list)
    account_holder: AccountHolder | None = None
    payment: PaymentSignals = field(default_factory=PaymentSignals)
    warnings: list[ExtractionWarning] = field(default_factory=list)
    withheld_websites: list[Contact] = field(default_factory=list)


def compute_status(office, license_number, contacts: list, ibans: list) -> str:
    channels = [c.normalized_value for c in contacts] + [i.normalized_value for i in ibans]
    if not office and not license_number and not channels:
        return "not_found"
    if office and channels and all(channels):
        return "ok"
    return "partial"


def _clip(text: str) -> str:
    text = clean_spaces(text)
    return text if len(text) <= SNIPPET_MAX else text[: SNIPPET_MAX - 1] + "…"


def _span_confidence(line: Line, nt: NormalizedText, start: int, end: int) -> float | None:
    if not line.char_confidence:
        return None
    values = [
        line.char_confidence[p]
        for p in nt.original_positions(start, end)
        if p < len(line.char_confidence) and not line.text[p].isspace()
    ]
    return min(values) if values else None


def _evidence(line: Line, input_type: str, conf: float | None = None) -> Evidence:
    return Evidence(
        snippet=_clip(line.text),
        section=line.section if input_type == "url" else None,
        line=line.line_no,
        bbox=line.bbox,
        ocr_confidence=round(conf, 3) if conf is not None else None,
    )


def _attribution(input_type: str, section: str, context: str) -> str:
    if input_type == "url":
        if section in ("header", "footer") or SUPPORT_KW.search(context):
            return "platform"
        return "advertiser" if section == "listing" else "unclear"
    return "unclear" if SUPPORT_KW.search(context) else "advertiser"


class _Extractor:
    def __init__(self, read: ReadResult, min_digit_confidence: float, withhold_websites: bool = False):
        self.withhold_websites = withhold_websites
        self.read = read
        self.min_digit_confidence = min_digit_confidence
        self.page_host = L.host_of(read.final_url) if read.final_url else None
        self.candidates: list[Candidate] = []
        self.names: list[tuple] = []
        self.licenses: list[tuple] = []
        self.ibans: list[tuple] = []
        self.holders: list[tuple] = []
        self.phrases: dict[str, list[tuple]] = {k: [] for k in ("urgency", "payment", "account", "new_account", "personal_account")}

    def run(self) -> ExtractionOutput:
        previous: Line | None = None
        for line in self.read.lines:
            self._line(line, previous)
            previous = line
        for link in self.read.links:
            self._link(link)
        return self._finish()

    def _add(self, line_or_link, nt, start, end, ctype, normalized, problem, context, conf=None, platform=None, confidence="high"):
        if isinstance(line_or_link, Line):
            line = line_or_link
            evidence = _evidence(line, self.read.input_type, conf)
            raw = nt.raw(start, end)
            source = line.source
            section = line.section
        else:
            link = line_or_link
            evidence = Evidence(
                snippet=_clip(link.text) if link.text else link.href,
                section=link.section,
                href=link.href,
            )
            raw = link.href
            source = link.source
            section = link.section
        if problem:
            confidence = "low"
        elif conf is not None and conf < LOW_OCR:
            confidence = "low" if confidence != "high" else "medium"
        contact = Contact(
            type=ctype,
            platform=platform,
            raw_value=raw,
            normalized_value=normalized,
            source=source,
            evidence=evidence,
            attribution=_attribution(self.read.input_type, section, context),
            confidence=confidence,
        )
        self.candidates.append(Candidate(contact, problem, start))

    def _line(self, line: Line, previous: Line | None):
        first = len(self.candidates)
        self._scan_line(line, previous)
        self.candidates[first:] = sorted(self.candidates[first:], key=lambda c: c.position)

    def _scan_line(self, line: Line, previous: Line | None):
        nt = normalize_text(line.text)
        text = nt.text
        platform_section = line.section in ("header", "footer")

        if not platform_section:
            for cand in find_office_names(nt):
                self.names.append((cand, line, nt))
        for lic in find_licenses(nt):
            if not platform_section:
                self.licenses.append((lic, line, nt))
        license_spans = [(h.start, h.end) for h in find_licenses(nt)]
        ibans = P.find_ibans(text, ocr=line.source == "ocr")
        iban_spans = [(h.start, h.end) for h in ibans]
        if not platform_section:
            self._payment_line(line, nt, ibans)

        emails = L.find_emails(text)
        email_spans = [(s, e) for s, e, _ in emails]
        urls = L.find_urls(mask(text, email_spans))
        url_spans = [(s, e) for s, e, _ in urls]
        handles = L.find_handles(mask(text, email_spans + url_spans))
        handle_spans = [(s, e) for s, e, _, _ in handles]

        for s, e, value in emails:
            self._add(line, nt, s, e, "email", value.lower(), None, text[:s])
        for s, e, value in urls:
            lc = L.classify_url(value)
            if lc:
                conf = _span_confidence(line, nt, s, e)
                normalized, problem = lc.normalized, lc.problem
                if normalized and conf is not None and conf < self.min_digit_confidence:
                    normalized, problem = None, "ambiguous_link"
                self._add(line, nt, s, e, lc.type, normalized, problem, text[:s], conf, lc.platform)
        for s, e, handle, platform in handles:
            confidence = "high" if platform != "unknown" else "medium"
            self._add(line, nt, s, e, "social", "@" + handle.lower(), None, text[:s], platform=platform, confidence=confidence)

        masked = mask(text, license_spans + email_spans + url_spans + handle_spans + iban_spans)
        prev_end = 0
        prev_types: list[str] | None = None
        hits = find_phones(masked)
        for i, hit in enumerate(hits):
            window = text[prev_end : hit.start]
            if i == 0 and previous is not None and not LETTER.search(window):
                prev_text = normalize_text(previous.text).text
                if len(prev_text) <= 30 and not re.search(r"\d{4}", prev_text):
                    window = prev_text + " " + window
            prev_end = hit.end
            has_wa = bool(WHATSAPP_KW.search(window))
            has_call = bool(CALL_KW.search(window))
            if not (has_wa or has_call) and i == len(hits) - 1:
                has_wa = bool(WHATSAPP_KW.search(text[hit.end : hit.end + 20]))
            if AD_ID_KW.search(window) and not (has_wa or has_call):
                prev_types = None
                continue
            if hit.kind != "valid" and not (hit.strong or has_wa or has_call or CONTACT_KW.search(window)):
                continue
            inherited = False
            if has_wa or has_call:
                types = ([hit.base_type] if has_call else []) + (["whatsapp"] if has_wa else [])
            elif prev_types and SEPARATORS_ONLY.match(window):
                types, inherited = prev_types, True
            else:
                types = [hit.base_type]
            prev_types = types
            conf = _span_confidence(line, nt, hit.start, hit.end)
            problem = None
            normalized = hit.e164
            if hit.kind == "ambiguous" or (conf is not None and conf < self.min_digit_confidence):
                problem, normalized = "ambiguous_phone", None
            elif hit.kind == "invalid":
                problem = "invalid_phone"
            confidence = "high" if (has_wa or has_call or inherited or CONTACT_KW.search(window)) else "medium"
            for ctype in types:
                self._add(line, nt, hit.start, hit.end, ctype, normalized, problem, window, conf, confidence=confidence)

    def _payment_line(self, line: Line, nt: NormalizedText, ibans):
        for hit in ibans:
            self.ibans.append((hit, line, nt))
        plain = P.strip_marks(nt)
        for hit in P.find_holders(plain):
            self.holders.append((hit, line, plain))
        finders = {
            "urgency": P.find_urgency,
            "payment": P.find_payment_words,
            "account": P.find_account_mentions,
            "new_account": P.find_new_account,
            "personal_account": P.find_personal_account,
        }
        for kind, finder in finders.items():
            for hit in finder(plain):
                self.phrases[kind].append((hit, line, plain))

    def _phrase(self, start: int, end: int, line: Line, nt: NormalizedText) -> Phrase:
        return Phrase(text=clean_spaces(nt.raw(start, end)), source=line.source, evidence=_evidence(line, self.read.input_type))

    def _phrase_list(self, kind: str) -> list[Phrase]:
        phrases, seen = [], set()
        for hit, line, nt in self.phrases[kind]:
            phrase = self._phrase(hit.start, hit.end, line, nt)
            if phrase.text not in seen:
                seen.add(phrase.text)
                phrases.append(phrase)
        return phrases

    def _ibans(self, warnings) -> list[Iban]:
        result, seen = [], set()
        for hit, line, nt in self.ibans:
            raw = nt.raw(hit.start, hit.end)
            key = hit.normalized or clean_spaces(raw)
            if key in seen:
                continue
            seen.add(key)
            conf = _span_confidence(line, nt, hit.start, hit.end)
            normalized, problem = hit.normalized, hit.problem
            if normalized and conf is not None and conf < self.min_digit_confidence:
                normalized, problem = None, "ambiguous_iban"
            confidence = "high" if normalized and hit.checksum_valid else "low"
            if confidence == "high" and conf is not None and conf < LOW_OCR:
                confidence = "medium"
            if problem:
                warnings.append(ExtractionWarning(code=problem, message=MESSAGES[problem], field=f"ibans[{len(result)}]"))
            result.append(
                Iban(
                    raw_value=raw,
                    normalized_value=normalized,
                    checksum_valid=hit.checksum_valid,
                    source=line.source,
                    evidence=_evidence(line, self.read.input_type, conf),
                    confidence=confidence,
                )
            )
        return result

    def _account_holder(self, warnings, has_account: bool) -> AccountHolder | None:
        if not self.holders or not has_account:
            return None
        hit, line, nt = self.holders[0]
        if len({h.normalized for h, _, _ in self.holders}) > 1:
            warnings.append(
                ExtractionWarning(code="multiple_account_holders", message=MESSAGES["multiple_account_holders"], field="account_holder")
            )
        conf = _span_confidence(line, nt, hit.start, hit.end)
        confidence = "low" if conf is not None and conf < LOW_OCR else "high"
        return AccountHolder(
            raw_value=nt.raw(hit.start, hit.end),
            normalized_value=hit.normalized,
            source=line.source,
            evidence=_evidence(line, self.read.input_type, conf),
            confidence=confidence,
            holder_type=hit.holder_type,
        )

    def _payment(self, holder: AccountHolder | None, has_account: bool) -> PaymentSignals:
        pay_words = self._phrase_list("payment")
        personal = self._phrase_list("personal_account")
        if holder and holder.holder_type == "person":
            personal.insert(0, Phrase(text=holder.raw_value, source=holder.source, evidence=holder.evidence))
        new_account = self._phrase_list("new_account")
        return PaymentSignals(
            payment_request=Signal(value=bool(pay_words and has_account), evidence=pay_words if has_account else []),
            personal_account=Signal(value=bool(personal), evidence=personal),
            new_account_claim=Signal(value=bool(new_account), evidence=new_account),
            urgency_phrases=self._phrase_list("urgency"),
        )

    def _link(self, link: Link):
        lc = L.classify_url(link.href)
        if lc is None:
            return
        if lc.type == "website":
            if link.section != "listing" or not link.href.lower().startswith(("http://", "https://")):
                return
            if self.page_host and L.host_of(link.href) == self.page_host:
                return
        self._add(link, None, 0, 0, lc.type, lc.normalized, lc.problem, link.text, platform=lc.platform)

    def _office(self, warnings):
        if not self.names:
            warnings.append(ExtractionWarning(code="office_name_not_found", message=MESSAGES["office_name_not_found"], field="office_name"))
            return None
        best_priority = max(c.priority for c, _, _ in self.names)
        cand, line, nt = next(item for item in self.names if item[0].priority == best_priority)
        best_key = name_key(cand.normalized)
        others = {
            c.normalized
            for c, _, _ in self.names
            if name_key(c.normalized) not in best_key and best_key not in name_key(c.normalized)
        }
        if others:
            warnings.append(
                ExtractionWarning(
                    code="multiple_office_names",
                    message=MESSAGES["multiple_office_names"] + ": " + "، ".join(sorted(others)),
                    field="office_name",
                )
            )
        conf = _span_confidence(line, nt, cand.start, cand.end)
        confidence = "high" if cand.method in ("labeled", "pattern", "english") else "medium"
        if conf is not None and conf < LOW_OCR:
            confidence = "low"
            warnings.append(ExtractionWarning(code="low_ocr_confidence", message=MESSAGES["low_ocr_confidence"], field="office_name"))
        return FieldValue(
            raw_value=nt.raw(cand.start, cand.end),
            normalized_value=cand.normalized,
            source=line.source,
            evidence=_evidence(line, self.read.input_type, conf),
            confidence=confidence,
        )

    def _license(self, warnings):
        if not self.licenses:
            return None
        hit, line, nt = self.licenses[0]
        if len({h.normalized for h, _, _ in self.licenses}) > 1:
            warnings.append(ExtractionWarning(code="multiple_license_numbers", message=MESSAGES["multiple_license_numbers"], field="license_number"))
        conf = _span_confidence(line, nt, hit.start, hit.end)
        normalized = hit.normalized
        confidence = "high"
        if conf is not None and conf < self.min_digit_confidence:
            normalized, confidence = None, "low"
            warnings.append(ExtractionWarning(code="low_ocr_confidence", message=MESSAGES["low_ocr_confidence"], field="license_number"))
        return FieldValue(
            raw_value=nt.raw(hit.start, hit.end),
            normalized_value=normalized,
            source=line.source,
            evidence=_evidence(line, self.read.input_type, conf),
            confidence=confidence,
        )

    def _contacts(self):
        contacts: list[Candidate] = []
        platform: list[Candidate] = []
        seen: dict[tuple, Candidate] = {}
        for cand in self.candidates:
            c = cand.contact
            bucket = "platform" if c.attribution == "platform" else "contacts"
            value = c.normalized_value or "raw:" + clean_spaces(c.raw_value)
            key = (bucket, c.type, value)
            if key in seen:
                existing = seen[key].contact
                if existing.attribution == "unclear" and c.attribution == "advertiser":
                    existing.attribution = "advertiser"
                continue
            seen[key] = cand
            (platform if bucket == "platform" else contacts).append(cand)
        return contacts, platform

    def _finish(self) -> ExtractionOutput:
        warnings: list[ExtractionWarning] = []
        office = self._office(warnings)
        license_number = self._license(warnings)
        contacts, platform = self._contacts()
        withheld = [c.contact for c in contacts if self.withhold_websites and c.contact.type == "website"]
        contacts = [c for c in contacts if c.contact not in withheld]
        ibans = self._ibans(warnings)
        has_account = bool(self.ibans or self.phrases["account"])
        holder = self._account_holder(warnings, has_account)
        payment = self._payment(holder, has_account)
        for i, cand in enumerate(contacts):
            path = f"contacts[{i}]"
            if cand.problem:
                warnings.append(ExtractionWarning(code=cand.problem, message=MESSAGES[cand.problem], field=path))
            if cand.contact.attribution == "unclear":
                warnings.append(
                    ExtractionWarning(code="contact_attribution_unclear", message=MESSAGES["contact_attribution_unclear"], field=path)
                )
        contact_models = [c.contact for c in contacts]
        return ExtractionOutput(
            status=compute_status(office, license_number, contact_models, ibans),
            office_name=office,
            license_number=license_number,
            contacts=contact_models,
            platform_contacts=[c.contact for c in platform],
            ibans=ibans,
            account_holder=holder,
            payment=payment,
            warnings=warnings,
            withheld_websites=withheld,
        )


def extract(read: ReadResult, min_digit_confidence: float = 0.6, withhold_websites: bool = False) -> ExtractionOutput:
    return _Extractor(read, min_digit_confidence, withhold_websites).run()
