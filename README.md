# صون: خدمة استخراج بيانات إعلانات الاستقدام

**صون** يساعد من يتعامل مع إعلان استقدام عمالة منزلية على معرفة: هل قناة التواصل أو الحساب البنكي في الإعلان يعود فعلًا للمكتب المذكور فيه؟ المحتالون ينتحلون أسماء مكاتب مرخّصة ويضعون أرقامهم أو حساباتهم.

المشروع ثلاثة أجزاء:

| الجزء | المسؤولية |
|---|---|
| **خدمة الاستخراج** (هذا المستودع) | تقرأ الإعلان (نص أو صورة أو رابط) وتستخرج ما ورد فيه فقط، مع الدليل لكل قيمة |
| الواجهة | ترسل الإعلان إلى `POST /extract` وتعرض النتيجة وتطلب التأكيد من المستخدم |
| المطابقة | تقارن المستخرَج بالقاعدة المرجعية وتصدر الحكم: يعود، لا يعود، لا نعرف |

**هذه الخدمة لا تصدر حكمًا.** ترجع لكل قيمة: النص كما ورد، والصيغة الموحّدة، والمصدر، والدليل، ومستوى الثقة.

الحقول المستخرجة:

- اسم المكتب ورقم الترخيص
- قنوات التواصل: جوال، ثابت، واتساب، بريد، موقع، حسابات تواصل
- الآيبان وصاحب الحساب
- مؤشرات الدفع: طلب تحويل، حساب شخصي، ادعاء تغيير الحساب، عبارات استعجال

العقد الكامل في [docs/CONTRACT.md](docs/CONTRACT.md)، وتسليم الواجهة والمطابقة في [docs/DEMO_HANDOFF.md](docs/DEMO_HANDOFF.md).

## مسارات الاستخراج

| المدخل | الحقول | الدومين | عند التعذّر |
|---|---|---|---|
| نص | `gpt-5-mini` بـJSON Schema صارم، ثم التحقق من وجود كل قيمة حرفيًا في الإعلان، ثم توحيد الأرقام والآيبان والدومين برمجيًا | من GPT بنفس التحقق | القواعد، مع `extractor.fallback_reason` وتنبيه `llm_fallback_rules` |
| صورة | Tesseract محلي (`ara+eng`) + قواعد الاستخراج | GPT Vision على الصورة الأصلية، بالتوازي مع Tesseract | الدومين المقروء من Tesseract يرجع بلا قيمة موحّدة مع `domain_needs_confirmation` |
| رابط | قواعد الاستخراج على HTML الصفحة | من الصفحة | `status: unreadable` مع اقتراح لصق النص أو رفع صورة |

`extractor` في كل رد يوضح المحرك الذي اشتغل فعلًا (`name`) والمطلوب (`requested`) وسبب الرجوع للقواعد (`fallback_reason`) وحالة قراءة الدومين في الصور (`domain_status`).

## التشغيل من نسخة جديدة

يحتاج Python 3.11 أو أحدث.

```powershell
git clone https://github.com/Hrlloo12/sawn-extraction.git
cd sawn-extraction
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
```

على Linux أو macOS:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
```

ثم عدّل `.env` (التفاصيل تحت)، وشغّل:

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- **التجربة من المتصفح:** `http://127.0.0.1:8000/docs`، ثم `POST /extract`، ثم **Try it out**.
- **الصحة:** `GET /health`.

```json
{"status": "ok", "schema_version": "1.2", "ocr_provider": "tesseract", "azure_configured": false,
 "extractor": "gpt", "image_domain_reader": "vision", "openai_configured": true,
 "openai_model": "gpt-5-mini", "vision_model": "gpt-5-mini"}
```

> **`127.0.0.1` و`localhost` للتشغيل المحلي فقط.** الواجهة المنشورة لا تصل إلى جهازك؛ تحتاج الخدمة منشورة على خادم بعنوان عام (مثل `https://api.your-domain.example`)، ويُضاف نطاق الواجهة إلى `CORS_ORIGINS`. على Windows استخدم `127.0.0.1` بدل `localhost`.

### Tesseract بالعربي والإنجليزي

الصور تُقرأ محليًا بـTesseract، فلا تخرج الصورة لأي جهة إلا GPT Vision لقراءة الدومين.

**Windows:**

```powershell
winget install --id UB-Mannheim.TesseractOCR -e
mkdir $HOME\tessdata
curl.exe -L -o $HOME\tessdata\ara.traineddata https://github.com/tesseract-ocr/tessdata_best/raw/4.1.0/ara.traineddata
copy "C:\Program Files\Tesseract-OCR\tessdata\eng.traineddata" $HOME\tessdata\
copy "C:\Program Files\Tesseract-OCR\tessdata\osd.traineddata" $HOME\tessdata\
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --tessdata-dir $HOME\tessdata --list-langs
```

آخر أمر يجب أن يطبع `ara` و`eng` و`osd`. ثم في `.env`:

```dotenv
TESSERACT_TESSDATA_DIR=C:/Users/<اسم المستخدم>/tessdata
TESSERACT_LANGS=ara+eng
```

**Linux (خادم Ubuntu أو Debian):**

```bash
sudo apt install tesseract-ocr tesseract-ocr-ara
tesseract --list-langs
```

`eng` يأتي مع الحزمة الأساسية، ويمكن ترك `TESSERACT_TESSDATA_DIR` فارغًا. **macOS:** `brew install tesseract tesseract-lang`.

تفاصيل إعادة القراءة ومعالجة الأرقام داخل السطر العربي في [docs/SETUP_TESSERACT.md](docs/SETUP_TESSERACT.md).

### متغيرات البيئة

القيم في `.env` (مستثنى من Git). [`.env.example`](.env.example) فيه قيم وهمية فقط.

| المتغير | الافتراضي | الوصف |
|---|---|---|
| `OPENAI_API_KEY` | فارغ | مفتاح OpenAI، على الخادم فقط. بدونه يعمل النص بالقواعد، ويرجع الدومين في الصور بحالة `not_configured` مع طلب تأكيد. طريقة الحصول عليه في [docs/SETUP_KEYS.md](docs/SETUP_KEYS.md). |
| `OPENAI_MODEL` | `gpt-5-mini` | نموذج النص |
| `OPENAI_VISION_MODEL` | فارغ (= `OPENAI_MODEL`) | نموذج قراءة الدومين من الصورة |
| `OPENAI_REASONING_EFFORT` | `low` | `minimal` أو `low` أو `medium` أو `high`، أو فارغ لنموذج بلا استدلال |
| `OPENAI_TIMEOUT_SECONDS` | `45` | مهلة طلب OpenAI |
| `EXTRACTOR` | `gpt` | محرك النص الافتراضي: `gpt` أو `rules` |
| `IMAGE_DOMAIN_READER` | `vision` | `vision` (GPT Vision) أو `ocr` (Tesseract وحده) |
| `OCR_PROVIDER` | `tesseract` في `.env.example` | `tesseract`، أو `azure`، أو `fixture` (قراءات محفوظة لصور `samples/images`، للاختبارات فقط) |
| `TESSERACT_CMD` | فارغ | مسار `tesseract` إذا لم يكن في `PATH` |
| `TESSERACT_TESSDATA_DIR` | فارغ | مجلد ملفات اللغات |
| `TESSERACT_LANGS` | `ara+eng` | لغات القراءة |
| `OCR_MIN_DIGIT_CONFIDENCE` | `0.6` | تحت هذه الثقة لا يُوحَّد الرقم ويُطلب تأكيده |
| `AZURE_DI_ENDPOINT`, `AZURE_DI_KEY` | وهمية | فقط مع `OCR_PROVIDER=azure` |
| `REFERENCE_DATA_DIR` | `team_data` | مجلد القاعدة التجريبية لـ`/check/phone` |
| `ALLOWED_DOMAINS` | `localhost,127.0.0.1` | النطاقات المسموح بقراءة روابطها |
| `CORS_ORIGINS` | `*` في الكود، و`http://localhost:3000,http://localhost:5173` في `.env.example` | عناوين الواجهة المسموح لها، مفصولة بفواصل. على الخادم المنشور ضع عنوان الواجهة المنشورة. |
| `MAX_IMAGE_BYTES`, `MAX_TEXT_CHARS` | `5000000`, `20000` | حدود المدخل |

**الخدمة تقرأ `.env` عند التشغيل فقط**، فأعد تشغيل uvicorn بعد أي تعديل. ومتغيرات البيئة في النظام تتقدم على `.env`.

## تجربة `POST /extract`

الطلب `multipart/form-data` بالحقول `input_type` (`text` أو `image` أو `url`)، و`value` للنص أو الرابط، و`file` للصورة.

### نص

```powershell
curl.exe -F input_type=text -F "value=<integration/examples/ad_text_reeshat.txt" http://127.0.0.1:8000/extract
```

على Windows أرسل النص العربي من ملف UTF-8 كما في المثال، لا من سطر الأوامر مباشرة.

رد فعلي من `gpt-5-mini` (مختصر؛ الكامل في [integration/examples/extract_response_text_gpt.json](integration/examples/extract_response_text_gpt.json)):

```json
{
  "schema_version": "1.2",
  "extractor": {"name": "gpt", "model": "gpt-5-mini", "requested": "gpt", "fallback_reason": null},
  "status": "ok",
  "input_type": "text",
  "office_name": {"raw_value": "مكتب ريشة الصباح للاستقدام", "normalized_value": "مكتب ريشة الصباح للاستقدام",
                  "source": "text", "evidence": {"snippet": "مكتب ريشة الصباح للاستقدام", "line": 1}, "confidence": "high"},
  "license_number": null,
  "contacts": [
    {"type": "mobile", "raw_value": "٠٥٠ ٠٠٠ ٠٠٧١", "normalized_value": "+966500000071",
     "source": "text", "evidence": {"snippet": "للاستفسار: ٠٥٠ ٠٠٠ ٠٠٧١", "line": 3}, "attribution": "advertiser", "confidence": "high"},
    {"type": "whatsapp", "raw_value": "+966 50 999 0505", "normalized_value": "+966509990505", "...": "..."},
    {"type": "website", "raw_value": "https://reeshat-sabah.example/contact", "normalized_value": "reeshat-sabah.example/contact", "...": "..."}
  ],
  "platform_contacts": [],
  "ibans": [{"raw_value": "SA96 0088 0000 0000 0000 9001", "normalized_value": "SA9600880000000000009001", "checksum_valid": true, "...": "..."}],
  "account_holder": {"raw_value": "ماجد ع.", "normalized_value": "ماجد ع.", "holder_type": "person", "confidence": "high", "...": "..."},
  "payment": {
    "payment_request": {"value": true, "evidence": [{"text": "لتأكيد الحجز اليوم، حوّلوا العربون على الآيبان التالي:", "...": "..."}]},
    "personal_account": {"value": true, "evidence": [{"text": "اسم صاحب الحساب: ماجد ع.", "...": "..."}]},
    "new_account_claim": {"value": false, "evidence": []},
    "urgency_phrases": [{"text": "لتأكيد الحجز اليوم،", "...": "..."}]
  },
  "warnings": [],
  "error": null,
  "timing_ms": {"read": 2, "extract": 11118, "total": 11120}
}
```

### صورة

```powershell
curl.exe -F input_type=image -F "file=@samples/images/ad10_riyada.png" http://127.0.0.1:8000/extract
```

رد فعلي من Tesseract والقواعد، مع GPT Vision للدومين (مختصر؛ الكامل في [integration/examples/extract_response_image_tesseract.json](integration/examples/extract_response_image_tesseract.json)):

```json
{
  "schema_version": "1.2",
  "extractor": {"name": "rules", "requested": "rules", "domain_reader": "gpt_vision",
                "domain_status": "none_found", "vision_model": "gpt-5-mini"},
  "status": "ok",
  "input_type": "image",
  "office_name": {"raw_value": "مكتب استقدام الريادة التجريبي", "source": "ocr", "confidence": "medium", "...": "..."},
  "contacts": [
    {"type": "mobile", "raw_value": "0570002020", "normalized_value": "+966570002020", "source": "ocr",
     "evidence": {"snippet": "جوال: 0570002020 / 0550001010 واتساب: 0570002020", "line": 2,
                  "bbox": [448.0, 121.0, 951.0, 121.0, 951.0, 218.0, 448.0, 218.0], "ocr_confidence": 0.922},
     "attribution": "advertiser", "confidence": "high"},
    {"type": "mobile", "raw_value": "0550001010", "normalized_value": "+966550001010", "...": "..."},
    {"type": "whatsapp", "raw_value": "0570002020", "normalized_value": "+966570002020", "...": "..."}
  ],
  "ibans": [],
  "warnings": [],
  "timing_ms": {"read": 2282, "extract": 347, "total": 2629}
}
```

من JavaScript:

```js
const form = new FormData();
form.append("input_type", "image");
form.append("file", fileInput.files[0]);
const result = await fetch(`${API_BASE}/extract`, { method: "POST", body: form }).then(r => r.json());
```

- HTTP 200 لكل نتائج القراءة، بما فيها `status: "unreadable"`.
- HTTP 422 فقط إذا الطلب نفسه ناقص، مثل `{"detail": {"code": "missing_value", "message": "أرسل نص الإعلان في الحقل value"}}`.
- **متى تطلب التأكيد من المستخدم:** كل قناة `normalized_value: null`، وتنبيهات `ambiguous_*` و`domain_needs_confirmation`. الجدول الكامل في [docs/CONTRACT.md](docs/CONTRACT.md).

## الربط مع المطابقة

```python
from integration.matcher_adapter import to_matcher_input

matcher_input = to_matcher_input(extract_response)
```

- المحوّل: [integration/matcher_adapter.py](integration/matcher_adapter.py)
- المخطط: [integration/matcher_input.schema.json](integration/matcher_input.schema.json)
- أمثلة فعلية (رد الاستخراج ومدخل المطابِق الناتج منه): [integration/examples/](integration/examples/)
- قاعدتا الديمو بعد المطابِق: [integration/demo_guards.py](integration/demo_guards.py)

## حزمة الفريق (غير منشورة)

القاعدة المرجعية التجريبية وصور حالات الفريق غير موجودة في المستودع العام. `/extract` يعمل بدونها. يحتاجها:

- `POST /check/phone`: يرجع HTTP 503 بالكود `reference_data_missing` بدونها.
- `scripts/run_team_cases.py` و`scripts/repeat_cases.py`.
- 32 اختبارًا لا تعمل بدونها، وتُتخطّى تلقائيًا.

لإضافتها محليًا:

1. فك `sawn-mock-data.zip` (من الفريق) في `team_data/`، بحيث يوجد `team_data/matcher.py` و`entities.json` و`reports.json` و`test_cases.json`.
2. ولّد صور الحالات: `.\.venv\Scripts\python scripts\render_team_images.py` (تُكتب في `samples/team_images/`).
3. صور الفريق الإضافية ونصوصها في `samples/team_v2_images/` إن وُجدت عندك.

المجلدات الثلاثة مستثناة في `.gitignore`.

## الاختبار والتقييم

```powershell
.\.venv\Scripts\python -m pytest
```

- بدون شبكة وبدون مفاتيح. OpenAI وAzure مُحاكيان بـ`MockTransport`، وصور `samples/images` تُقرأ من قراءات OCR محفوظة.
- اختبار Tesseract الحقيقي يُتخطّى إذا لم يكن مثبتًا مع حزمة `ara`.
- مع حزمة الفريق: 212 اختبارًا. بدونها: 180 تنجح، و32 تُتخطّى.

سكربتات أخرى:

| السكربت | الغرض |
|---|---|
| `scripts/demo_site.py` | صفحات إعلانات تجريبية على `http://127.0.0.1:8100` لتجربة مسار الرابط |
| `scripts/evaluate.py` | تقييم العينات الاثنتي عشرة ومجموعة holdout، ويحتاج الخادمين شغّالين |
| `scripts/ocr_preview.py` | يطبع ما قرأه OCR والدومين الذي قرأه GPT Vision لصورة |
| `scripts/run_team_cases.py` | حالات الفريق على الخدمة الحية ثم المطابِق (يحتاج حزمة الفريق) |
| `scripts/build_samples.py`, `scripts/make_examples.py` | يعيدان توليد العينات وأمثلة `/docs` |

التقارير تُكتب في `reports/`، وهو مستثنى من Git.

## البنية

```
app/
  main.py               POST /extract, POST /check/phone, GET /health
  schemas.py            عقد Pydantic
  config.py             متغيرات البيئة
  readers/              نص، رابط، صورة (Tesseract / Azure / محفوظ)
  extraction/           القواعد: normalize, phones, links, office, payment, pipeline
                        GPT: openai_client, llm (النص), vision (دومين الصورة)
integration/            matcher_adapter, demo_guards, المخطط، أمثلة
docs/                   العقد، التسليم، إعداد المفاتيح وTesseract، أمثلة الردود
samples/                إعلانات تجريبية مصطنعة: نصوص، صور، صفحات، قراءات OCR
scripts/                تشغيل وتقييم وتوليد عينات
tests/
```

## قرارات تقنية

- **GPT للنص:** OpenAI Responses مع Structured Outputs. كل رقم أو آيبان أو رابط لا يوجد حرفيًا في الإعلان يُستبعد مع `llm_value_not_in_source`، فلا تُضاف قيم من خارج الإعلان.
- **GPT Vision للدومين فقط:** Tesseract يخلط بين q وg في الدومينات اللاتينية. ناتج Vision لا يغيّر أي حقل آخر، ولا يُصحَّح من أي قاعدة مرجعية.
- **Tesseract مع إعادة قراءة:** الأرقام واللاتيني داخل السطر العربي تُقرأ مرة ثانية بالإنجليزي. إذا اختلفت القراءتان تنخفض الثقة، فلا تُوحَّد القيمة وتُطلب من المستخدم.
- **حماية الرابط:** قائمة نطاقات مسموحة تُفحص عند كل تحويل (حد 5 تحويلات)، ومنع العناوين الداخلية بعد حل DNS، ومهلة 8 ثوانٍ، وحد 2MB. إعادة ربط DNS بين الفحص والاتصال غير مغطاة.
- **المفاتيح:** في `.env` على الخادم فقط، ولا تظهر في الردود. أي جزء من مفتاح في رسالة خطأ من OpenAI يُستبدل بـ`sk-***`.

## حدود معروفة

- **الأرقام في الصور:** مع Tesseract الحقيقي قد يُفقد رقم بأرقام هندية (٠٥٠…) أو رقم مجموعاته مفصولة بمسافات داخل سطر عربي، أحيانًا بدون تنبيه، وقد يُعاد ترتيب مجموعاته خطأً. الاختبارات الآلية تستخدم قراءات OCR محفوظة فلا تكشف ذلك. عند الشك الصق نص الإعلان.
- **اسم المكتب في الصور:** «للاستقدام» في سطر منفصل قد تسقط من الاسم، وخطأ حرف واحد ممكن.
- **صاحب الحساب في الصور:** صيغة «حساب الأستاذ …» أو «باسم …» في الصور لا تُستخرج دائمًا، بينما يستخرجها مسار النص.

## البيانات

كل الأسماء والأرقام والآيبانات والروابط في المستودع مصطنعة، والنطاقات `.example`. لا توجد بيانات حقيقية.
