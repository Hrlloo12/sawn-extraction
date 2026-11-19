# عقد الربط: `POST /extract` (الإصدار 1.2)

خدمة الاستخراج تقرأ الإعلان وترجع ما ورد فيه فقط: اسم المكتب، رقم الترخيص، قنوات التواصل، والحسابات البنكية ومؤشرات الدفع، مع القيمة الأصلية والقيمة الموحّدة والمصدر والدليل.
**لا تقرر الخدمة نتيجة المطابقة**؛ «تعود للجهة / توجد أدلة أنها لا تعود / المعلومات غير كافية» قرار خدمة المطابقة.

## الطلب

`multipart/form-data`

| الحقل | إلزامي | القيم |
|---|---|---|
| `input_type` | نعم | `text` أو `url` أو `image` |
| `value` | مع `text` و`url` | نص الإعلان (حتى 20,000 حرف) أو رابطه |
| `file` | مع `image` | JPEG أو PNG أو WEBP أو HEIF أو BMP أو TIFF، حتى 5MB |
| `extractor` | لا | لمسار النص فقط: `gpt` (الافتراضي من `EXTRACTOR`) أو `rules`. الصورة والرابط بالقواعد دائمًا، والدومين في الصورة من GPT Vision حسب `IMAGE_DOMAIN_READER`. |

**التغييرات منذ 1.0 إضافية فقط:** كل حقول 1.0 بنفس الاسم والمعنى، والواجهة التي تقرأ حقول 1.0 تعمل بدون تعديل، لكن لا تفحص `schema_version` حرفيًا.

- **1.1:** أُضيفت `extractor` و`ibans` و`account_holder` و`payment`.
- **1.2:**
  - مسار النص بـGPT مع رجوع للقواعد بدل 503.
  - دومين الصورة من GPT Vision.
  - حقول جديدة داخل `extractor`، وأكواد تنبيه جديدة.

حقل `file` الفارغ مع طلب نصي يُتجاهل (Swagger يرسله أحيانًا).

```bash
curl -F input_type=text  -F "value=<samples/text/ad01_waha.txt"            http://127.0.0.1:8000/extract
curl -F input_type=url   -F value=http://127.0.0.1:8100/ads/ad01_waha.html  http://127.0.0.1:8000/extract
curl -F input_type=image -F file=@samples/images/ad05_nakheel.png          http://127.0.0.1:8000/extract
```

```js
const API_BASE = import.meta.env.VITE_SAWN_API ?? "http://127.0.0.1:8000";

async function extract(form) {
  const res = await fetch(`${API_BASE}/extract`, { method: "POST", body: form });
  if (res.status === 422) throw (await res.json()).detail;
  return res.json();
}

const textForm = new FormData();
textForm.append("input_type", "text");
textForm.append("value", adText);

const imageForm = new FormData();
imageForm.append("input_type", "image");
imageForm.append("file", fileInput.files[0]);
```

- لا تضبط `Content-Type` يدويًا؛ المتصفح يضيف حدود `multipart` بنفسه.
- `127.0.0.1:8000` للتشغيل المحلي فقط. الواجهة المنشورة تحتاج عنوان Backend منشورًا، ويُضاف نطاقها إلى `CORS_ORIGINS` في الخادم.

## الرد

HTTP 200 لكل نتائج القراءة، بما فيها تعذّر القراءة (`status: "unreadable"`). HTTP 422 فقط إذا الطلب نفسه ناقص، ويرجع `{"detail": {"code", "message"}}` بالأكواد `missing_value` و`missing_file` و`invalid_input_type` و`text_too_long` و`invalid_form` و`invalid_extractor`.
تعذّر OpenAI لا يرجع خطأ HTTP. مسار النص يرجع للقواعد، ومسار الصورة يكمل بدون دومين مؤكد، وكلاهما يوضح السبب في `extractor` و`warnings`.

| الحقل | الوصف |
|---|---|
| `schema_version` | `"1.2"` |
| `request_id` | معرّف الطلب |
| `extractor` | `{name, model, requested, fallback_reason, domain_reader, domain_status, vision_model}`، تفاصيله أدناه |
| `status` | `ok`: اسم المكتب وقناة موحّدة واحدة على الأقل وكل القنوات موحّدة (الآيبان يُحسب قناة). `partial`: بيانات ناقصة (اسم غائب أو رقم ملتبس). `not_found`: قُرئ المدخل ولا توجد بيانات. `unreadable`: تعذّرت القراءة، والتفاصيل في `error`. |
| `input_type` | نوع المدخل |
| `input_url`, `final_url` | الرابط المُدخل والرابط النهائي بعد التحويلات (مسار الرابط فقط) |
| `office_name` | `FieldValue` أو `null` |
| `license_number` | `FieldValue` أو `null`. لا يُستخرج إلا مع كلمة صريحة (ترخيص، رخصة، License). السجل التجاري لا يُعتبر ترخيصًا. |
| `contacts` | قنوات المعلن، والقنوات غير محسومة النسبة (`attribution: "unclear"`) |
| `platform_contacts` | قنوات المنصة: رأس الصفحة، والتذييل، والدعم الفني. **لا تدخل المطابقة.** |
| `ibans` | قائمة `Iban`، انظر أدناه |
| `account_holder` | `FieldValue` مع `holder_type` (`organization` أو `person`)، أو `null`. يُرجع فقط إذا ذُكر حساب أو آيبان. |
| `payment` | `{payment_request, personal_account, new_account_claim, urgency_phrases}`، انظر أدناه |
| `warnings` | قائمة `{code, message, field}` |
| `error` | `{code, message, suggestion}` عند `unreadable` فقط |
| `timing_ms` | `{read, extract, total}` |

### `FieldValue` (اسم المكتب ورقم الترخيص)

`raw_value` كما ورد في الإعلان، و`normalized_value` بأرقام إنجليزية ومسافات موحّدة، و`source`، و`evidence`، و`confidence` (`high` أو `medium` أو `low`).

### `Contact`

| الحقل | القيم |
|---|---|
| `type` | `mobile`, `landline`, `toll_free` (800/9200), `whatsapp`, `email`, `website`, `social` |
| `platform` | مع `social`: `instagram`, `x`, `snapchat`, `tiktok`, `telegram`, `facebook`, `unknown`. وإلا `null`. |
| `raw_value` | كما ورد، مثل `٠٥٥ ٠٠٠ ٠١٠١` أو `https://wa.me/966550000101` |
| `normalized_value` | الهواتف والواتساب بصيغة E.164 (`+966550000101`). البريد بحروف صغيرة. الموقع بالصيغة `domain/path` بدون `www` وبدون معاملات التتبع. الحساب بالصيغة `@handle`. **تكون `null` إذا كانت القراءة ملتبسة أو الرقم غير صالح، ولا نخمّن.** |
| `source` | `text`, `ocr` (من Azure أو Tesseract حسب `OCR_PROVIDER`), `page_text`, `page_title`, `page_meta`, `page_link` |
| `evidence` | `snippet` (السطر أو نص الزر)، `section` (`listing`, `header`, `footer`, `unknown` لمسار الرابط)، `line`، `bbox` (مضلع السطر في الصورة)، `ocr_confidence`، `href` |
| `attribution` | `advertiser`، أو `unclear` (رقم في صفحة بدون قسم واضح، أو مع كلمة «دعم/خدمة العملاء» في نص منسوخ)، أو `platform` (في `platform_contacts` فقط) |
| `confidence` | `high` مع كلمة دالة (جوال، واتساب، اتصال، للتواصل) أو رابط. `medium` لرقم بدون سياق. `low` لقراءة ضعيفة أو ملتبسة. |

نفس الرقم يظهر مرتين إذا ورد كقناتين، مثل `mobile` و`whatsapp` لـ«جوال وواتساب». التكرار من نفس النوع يُحذف.

### `extractor`

| الحقل | القيم |
|---|---|
| `name` | المحرك الذي استخرج الحقول فعلًا: `gpt` أو `rules` |
| `requested` | المحرك المطلوب. يختلف عن `name` عند الرجوع للقواعد |
| `model` | نموذج OpenAI عند `name=gpt` |
| `fallback_reason` | مثل `llm_failed: OpenAI HTTP 429: ...` أو `llm_not_configured: ...` أو `llm_timeout: ...`. أي جزء من مفتاح يظهر كـ`sk-***` |
| `domain_reader` | للصورة فقط: `gpt_vision` أو `ocr` |
| `domain_status` | للصورة مع `gpt_vision`: `read`، `none_found` (لا دومين في الصورة)، `illegible`، `failed`، `not_configured` |
| `vision_model` | نموذج GPT Vision المستخدم |

### الدومين في الصورة

- الدومين من GPT Vision على الصورة الأصلية، في `contacts` بـ`type: website` و`source: ocr`، و`evidence.snippet` هو النص كما نسخه النموذج.
- يُفحص بصيغة الدومين فقط ولا يُصحَّح، وكل الحقول الأخرى من Tesseract والقواعد.
- إذا فشل Vision أو كان الرابط غير واضح، الرابط الذي قرأه Tesseract يظهر بـ`normalized_value: null` وتنبيه `domain_needs_confirmation`، فلا يدخل المطابقة إلا بعد تأكيد المستخدم.

### `Iban`

| الحقل | القيم |
|---|---|
| `raw_value` | كما ورد، مثل `SA40 0009 0000 0000 0000 0163` |
| `normalized_value` | `SA` ثم 22 رقمًا بلا مسافات وبأرقام إنجليزية. `null` إذا كان الطول غير صحيح أو قراءة OCR ملتبسة. |
| `checksum_valid` | نتيجة فحص mod 97. الآيبان الذي يفشل الفحص يبقى موحّدًا مع تنبيه `iban_checksum_invalid`. |
| `source`, `evidence`, `confidence` | مثل `Contact` |

### `payment`

كل مؤشر `{value: bool, evidence: [Phrase]}`، و`Phrase` هي `{text, source, evidence}` حيث `text` العبارة كما وردت.

| المؤشر | متى يكون `true` |
|---|---|
| `payment_request` | كلمة دفع أو تحويل (حوّل، الدفع، السداد، رسوم، العربون، فاتورة...) مع ذكر حساب أو آيبان. «الدفع عبر منصة مساند» بلا حساب لا يُحسب. |
| `personal_account` | صاحب الحساب شخص (`account_holder.holder_type = person`)، أو نص صريح مثل «حساب شخصي». |
| `new_account_claim` | «تم تغيير/تحديث حسابنا»، «الحساب الجديد»، «حساب بنكي جديد». «رقمنا الجديد» لهاتف لا يُحسب. |
| `urgency_phrases` | قائمة `Phrase`: عرض خاص أو محدود، «لأول N عملاء»، «احجز الآن قبل نفاد...»، فعل + اليوم/الآن («حوّلها اليوم»)، وإعلان تغيير الحساب. |

### أكواد التنبيه (`warnings`)

| الكود | المعنى |
|---|---|
| `office_name_not_found` | لم يُعثر على اسم مكتب واضح |
| `multiple_office_names` | ظهر أكثر من اسم، أُرجع الأوضح |
| `multiple_license_numbers` | ظهر أكثر من رقم ترخيص |
| `ambiguous_phone` | رقم مقروء جزئيًا أو ثقة OCR منخفضة، و`normalized_value` تكون `null` |
| `invalid_phone` | رقم بصيغة سعودية لكن طوله أو بادئته غير صالحة |
| `unresolved_link` | رابط واتساب بدون رقم |
| `whatsapp_group` | رابط مجموعة واتساب |
| `contact_attribution_unclear` | لا يمكن التأكد أن القناة للمعلن |
| `low_ocr_confidence` | ثقة OCR منخفضة للاسم أو الترخيص |
| `ambiguous_link` | رابط أو حساب مقروء من صورة بثقة منخفضة، و`normalized_value` تكون `null` |
| `invalid_iban` | آيبان بطول غير صحيح، و`normalized_value` تكون `null` |
| `iban_checksum_invalid` | الآيبان لا يجتاز فحص mod 97 |
| `ambiguous_iban` | قراءة OCR ملتبسة للآيبان |
| `multiple_account_holders` | ظهر أكثر من صاحب حساب |
| `llm_value_not_in_source` | مع `gpt`: قيمة من النموذج غير موجودة في نص الإعلان. القناة تُستبعد، والاسم يبقى بثقة `low`. |
| `llm_value_aligned_to_source` | مع `gpt`: الاسم لم يرد حرفيًا، فاستُبدل بأقرب نص مطابق في الإعلان |
| `llm_fallback_rules` | تعذّر GPT واستُخدمت القواعد. السبب في `extractor.fallback_reason` |
| `domain_needs_confirmation` | دومين في الصورة غير مؤكد القراءة. اطلب لصق الرابط أو تأكيده |
| `domain_reader_unavailable` | تعذّر GPT Vision |
| `invalid_domain` | ما قرأه Vision ليس بصيغة دومين صالحة |

### أكواد تعذّر القراءة (`error.code`)

| الكود | المسار | `suggestion` |
|---|---|---|
| `invalid_url` | رابط | `paste_text` |
| `domain_not_allowed` | رابط (يشمل وجهات التحويل) | `upload_image` |
| `blocked_address` | رابط يشير إلى عنوان داخلي | `upload_image` |
| `fetch_failed` | خطأ HTTP أو اتصال | `upload_image` |
| `timeout` | انتهاء المهلة | `upload_image` أو `paste_text` |
| `login_required` | 401/403 أو صفحة دخول | `upload_image` |
| `response_too_large` | صفحة أكبر من 2MB | `upload_image` |
| `unsupported_content` | الرابط ليس صفحة HTML | `upload_image` |
| `empty_page` | صفحة بلا نص، غالبًا تحتاج JavaScript | `upload_image` |
| `unsupported_file`, `file_too_large` | صورة | `paste_text` |
| `ocr_not_configured` | مزوّد OCR غير جاهز: لا يوجد مفتاح Azure، أو Tesseract أو حزمة `ara` غير مثبتة، أو لا توجد قراءة محفوظة للصورة | `paste_text` |
| `ocr_failed` | فشل خدمة OCR | `paste_text` |

## أمثلة ردود فعلية

في [docs/examples/](examples/)، وتظهر أيضًا في `/docs` تحت `POST /extract → Responses → Examples`:

| الملف | الحالة |
|---|---|
| `response_text_ok.json` | نص كامل، أرقام عربية، جوال وواتساب |
| `response_text_no_office_name.json` | اسم غائب، `partial` |
| `response_url_platform_numbers_separated.json` | صفحة فيها أرقام المعلن وأرقام دعم المنصة |
| `response_url_whatsapp_button_without_visible_number.json` | الرقم موجود فقط في رابط زر واتساب |
| `response_url_attribution_unclear.json` | صفحة بدون أقسام: `attribution: unclear` |
| `response_image_ambiguous_number.json` | صورة غير واضحة: الرقم `null` مع `bbox` و`ocr_confidence` |
| `response_url_login_required.json` | صفحة تطلب دخول |
| `response_url_fetch_failed.json` | خطأ في الخادم |

## صيغة المطابِق (`team_data/matcher.py`)

`integration/matcher_adapter.py` يحوّل الرد إلى صيغة مدخل المطابِق، والمخطط الرسمي لها في `integration/matcher_input.schema.json`. حزمة الفريق (`team_data/`) غير منشورة في المستودع؛ انظر README.

مثال فعلي من تشغيل حي بـ`gpt-5-mini` على إعلان تجريبي: الرد في [integration/examples/extract_response_text_gpt.json](../integration/examples/extract_response_text_gpt.json)، ومدخل المطابِق الناتج منه في [integration/examples/matcher_input_text_gpt.json](../integration/examples/matcher_input_text_gpt.json):

```json
{
  "office_name": "مكتب ريشة الصباح للاستقدام",
  "phones": ["966500000071", "966509990505"],
  "urls": ["reeshat-sabah.example"],
  "ibans": ["SA9600880000000000009001"],
  "account_holder": "ماجد ع.",
  "personal_account": true,
  "new_account_claim": false,
  "urgency_phrases": ["لتأكيد الحجز اليوم،"],
  "payment_request": true,
  "ocr_urls": [],
  "unconfirmed": []
}
```

```powershell
.\.venv\Scripts\python integration\matcher_adapter.py integration\examples\extract_response_text_gpt.json
```

الحقول:

- `phones`: كل `contacts` من الأنواع `mobile`, `landline`, `toll_free`, `whatsapp` بقيمة موحّدة، بدون `+` (`966500000011`). مكرر الرقم (جوال وواتساب) يظهر مرة.
- `urls`: الدومين فقط من قنوات `website`.
- `ibans`: `normalized_value` غير `null`.
- القنوات التي `attribution: unclear` **تدخل افتراضيًا** (`include_unclear=True`) حتى يُتفق عليها. `platform_contacts` لا تدخل أبدًا.
- `payment_request` يُرسل كحقل إضافي يتجاهله المطابِق الحالي.
- `ocr_urls`: الدومينات التي قُرئت من صورة، لتفرّقها قاعدة `ocr_domain_mismatch`.
- `unconfirmed`: قنوات قُرئت بثقة منخفضة (`normalized_value: null`) ولم تدخل المطابقة حتى يؤكدها المستخدم.

## لفهد: ما يدخل المطابقة

- اقرأ `office_name.normalized_value` و`license_number.normalized_value`، وقد يكون أي منهما `null`.
- طابق `contacts` فقط، وتجاهل `platform_contacts`.
- القناة التي `normalized_value: null` لا تُطابق، وتُعرض للمستخدم كتنبيه.
- القناة التي `attribution: "unclear"` قرارها عندك، وهي نقطة اتفاق.
- مثال تحويل الرد إلى مدخلات المطابقة في [integration/consumer_example.py](../integration/consumer_example.py).
- حالات النتائج الثلاث في [integration/matching_cases.json](../integration/matching_cases.json)، مع بيانات مرجعية مصطنعة في [integration/reference_data.sample.json](../integration/reference_data.sample.json).

## لعارف: العرض

- `evidence.snippet` هو النص الذي تعرضه بجانب كل قيمة.
- `evidence.bbox` مضلع بثماني قيم بالبكسل على الصورة الأصلية، ويُرسم كإطار.
- `source` و`section` يوضحان مصدر القيمة: نص، أو OCR، أو صفحة (إعلان، رأس، تذييل)، أو زر.
- عند `status: "unreadable"` اعرض `error.message`، واقترح البديل حسب `error.suggestion`.

### متى تطلب التأكيد من المستخدم

| في الرد | اعرض |
|---|---|
| قناة أو آيبان `normalized_value: null` | `raw_value` في حقل قابل للتعديل مع زر «تأكيد». لا تعتبرها قناة صالحة قبل التأكيد. |
| `warnings` فيها `ambiguous_phone` أو `ambiguous_iban` أو `ambiguous_link` | نفس السابق، والحقل محدد في `warning.field` (مثل `contacts[1]`). |
| `domain_needs_confirmation` | «أكّد الرابط أو الصقه»، ثم أرسله بـ`input_type=url` أو `text`. |
| `office_name.confidence = "low"` أو `low_ocr_confidence` | الاسم مع طلب التحقق منه. |
| `office_name_not_found` | اطلب اسم المكتب، أو اكتفِ بالقنوات. |
| `extractor.name ≠ extractor.requested` | «تم الاستخراج بالقواعد» كمعلومة، بدون إيقاف المستخدم. |

القيمة المؤكدة تُرسل مرة ثانية إلى `/extract` بـ`input_type=text`، فتعود موحّدة بنفس العقد، أو الرقم وحده إلى `/check/phone`.

## `POST /check/phone`: فحص رقم وحده (ديمو)

يفحص رقمًا واحدًا مقابل القاعدة التجريبية، بدون اسم مكتب أو نص إعلان:

1. يوحّد الرقم بنفس كود `/extract`. يقبل `05...` و`+966...` و`966...` والأرقام العربية.
2. يمرره إلى `team_data/matcher.py` كما هو، مع `entities.json` و`reports.json`.

```bash
curl -F phone=0500000071 http://127.0.0.1:8000/check/phone
```

| الحقل | القيم |
|---|---|
| `result` | `registered`: مسجّل لمكتب. `unregistered`: غير مسجّل ولا مُبلَّغ عنه. `reported`: غير مسجّل ومُبلَّغ عنه أنه ينتحل مكتبًا |
| `office` | `{id, name_ar, name_en, license_no, city}` للمكتب المسجّل له الرقم، أو `null` |
| `message` | جملة جاهزة للعرض، مثل «الرقم مسجّل في القاعدة التجريبية لـمكتب ريشة الصباح للاستقدام» أو «الرقم غير مسجّل في القاعدة التجريبية» |
| `matcher_result` / `verdict` | نتيجة `matcher.py` للقناة (`match`، `no_match`، `unregistered`) وحكمه |
| `report` | بلاغ الانتحال إن وُجد، مع المكتب المُنتحَل |
| `normalized_value`, `phone_type` | الرقم بصيغة E.164 ونوعه |

- HTTP 422 بالكود `missing_value` إذا لم يُرسل `phone`.
- HTTP 422 بالكود `invalid_phone` إذا لم يكن الرقم بصيغة سعودية صالحة.
- HTTP 503 بالكود `reference_data_missing` إذا لم توجد حزمة الفريق في `REFERENCE_DATA_DIR` (الافتراضي `team_data`).
