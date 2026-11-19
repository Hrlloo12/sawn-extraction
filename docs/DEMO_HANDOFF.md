# تسليم الديمو لعارف وفهد

## التشغيل عند عارف

```powershell
git clone https://github.com/Hrlloo12/sawn-extraction.git
cd sawn-extraction
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

حزمة الفريق (`team_data/`) وصور الحالات غير منشورة في المستودع. `/extract` يعمل بدونها؛ `/check/phone` وسكربتات حالات الفريق تحتاجها، وطريقة إضافتها في README.

1. **ثبّت Tesseract وحزمة العربي** حسب [SETUP_TESSERACT.md](SETUP_TESSERACT.md)، وضع مسار المجلد في `TESSERACT_TESSDATA_DIR`.
2. **ضع `OPENAI_API_KEY` في `.env` على جهاز الخادم فقط.** لا يُرسل للواجهة، ولا يُكتب في الكود أو Git.
   - بدون مفتاح يعمل النص بالقواعد.
   - ويرجع الدومين في الصور بحالة `not_configured` مع طلب تأكيد من المستخدم.
3. **اترك القيم الافتراضية:** `EXTRACTOR=gpt` و`IMAGE_DOMAIN_READER=vision` و`OCR_PROVIDER=tesseract`.
4. **شغّل الخدمة:**

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`GET /health` يجب أن يُظهر:

```json
{"schema_version": "1.2", "extractor": "gpt", "image_domain_reader": "vision", "ocr_provider": "tesseract", "openai_configured": true}
```

## ما يتغير في الواجهة

**صيغة الرد إضافية فقط:** `schema_version` صار `1.2`، وكل الحقول القديمة كما هي.

| الحقل | ماذا تعرض |
|---|---|
| `extractor.name` / `extractor.requested` | إذا اختلفا، فقد تعذّر GPT واستُخدمت القواعد. اعرض «تم الاستخراج بالقواعد» دون إيقاف المستخدم |
| `extractor.domain_status` | للصور: `read`، `none_found`، `illegible`، `failed`، `not_configured` |
| `contacts[i].normalized_value = null` | قناة ملتبسة: اعرض `raw_value` و`warnings` للمستخدم ليؤكدها أو يصححها، ولا تعتبرها قناة صالحة |
| تنبيه `domain_needs_confirmation` | «أكّد الرابط أو الصقه»: حقل نص يُرسل بـ`input_type=url` أو `text` |

**الزمن المتوقع (الوسيط على الحالات العشرين):**
- النص: حوالي 8 ثوانٍ، لأن GPT يستغرق وقته. اعرض مؤشر تحميل.
- الصورة: حوالي 2.5 ثانية.
- الرابط: أقل من 0.1 ثانية.

## لفهد (المطابقة)

- **المحوّل:** `integration/matcher_adapter.py`، دالة `to_matcher_input(response, include_unclear=True)`.
- **المخطط:** `integration/matcher_input.schema.json`.
- **مثال فعلي:** `integration/examples/extract_response_text_gpt.json`، ومدخل المطابِق الناتج منه `integration/examples/matcher_input_text_gpt.json`. تفاصيل الحقول في `CONTRACT.md`.

`matcher.py` لم يتغير. القاعدتان طبقة بعده في `integration/demo_guards.py`:

```python
mi = to_matcher_input(extract_response)
entity, channels, verdict = matcher.check(mi, entities, reports)
decision = apply_demo_guards(mi, entity, channels, verdict, entities, reports)
```

`mi["ocr_urls"]` يحدد الدومينات المقروءة من صورة.

1. **`ocr_domain_mismatch`**
   - **الشرط:** الحكم «لا يعود»، وكل قناة `no_match` دومين من صورة، غير مُبلَّغ عنه وغير مسجّل لجهة أخرى.
   - **النتيجة:** «لا نعرف» مع طلب لصق الرابط أو تأكيده.
2. **`unconfirmed_channel`**
   - **الشرط:** الحكم «يعود»، وفي `mi["unconfirmed"]` قناة مقروءة بثقة منخفضة لم تدخل المطابقة.
   - **النتيجة:** «لا نعرف» مع طلب تأكيدها.
   - **السبب:** في T22 الرقم المنتحل على ملصق النافذة يُقرأ بثقة 47%. بدون هذه القاعدة يحكم المطابِق «يعود» من الرقم الرسمي وحده.
3. **`no_valid_channel`**
   - **الشرط:** لا رقم ولا دومين ولا آيبان صالح.
   - **النتيجة:** «لا نعرف» مع طلب لصق النص أو رفع صورة أوضح.

النتائج على صور الحالات العشرين في المسار المعتمد (من تقرير محلي غير منشور):

| الحالة | الدومين | المطابِق | النهائي |
|---|---|---|---|
| T02 | Vision قرأ `sana-shurooq.example` صحيحًا | يعود | **يعود** (كان «لا يعود» خطأ مع Tesseract وحده) |
| T03 | لا دومين، والآيبان ملتف على سطرين فصار `null` | يعود بلا قنوات | **لا نعرف** + اطلب النص |
| T09 | Vision قرأ النطاق المزوّر صحيحًا | لا يعود | **لا نعرف** + أكّد الرابط، لأنه من صورة |

## فحص رقم وحده

`POST /check/phone` بحقل `phone` في `FormData`:
- يرجع اسم المكتب إذا كان الرقم مسجّلًا.
- أو «الرقم غير مسجّل في القاعدة التجريبية».
- أو تنبيه انتحال إذا كان الرقم مُبلَّغًا عنه.

اعرض `message` كما هو، و`office.name_ar` إذا لم يكن `null`. التفاصيل في `CONTRACT.md`.

## خطوة التأكيد في الواجهة (عارف)

اختبرتها عبر الـAPI. الواجهة نفسها ليست في هذا المستودع.

1. **الرفع:** `/extract` بصورة.
   - كل قناة `normalized_value` فيها قيمة: اعرضها كمقروءة.
   - كل قناة `normalized_value: null`، وهي نفسها `mi["unconfirmed"]`: اعرض `raw_value` في حقل قابل للتعديل مع زر «تأكيد».
2. **بعد التأكيد:** وحّد القيمة.
   - الطريقة الأسهل: أرسل الرقم المؤكد إلى `/check/phone` لعرض حالته.
   - ولقرار الإعلان: أضفه إلى `phones` في مدخل المطابِق وأعد `matcher.check` ثم `apply_demo_guards`.
3. **T22:** يظهر `0509990505` للتأكيد في كل مرة. بعد التأكيد يصير الحكم «لا يعود» (R06).
4. **خطة الاحتياط:** إذا تعثرت الصورة وقت العرض، الصق نص الإعلان في `input_type=text`. نصوص صور الفريق في `samples/team_v2_images/transcriptions.json` عند من عنده الحزمة (غير منشورة).

تفاصيل متى تطلب التأكيد لكل نوع تنبيه في جدول «متى تطلب التأكيد من المستخدم» في `CONTRACT.md`.
