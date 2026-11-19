# إعداد مفتاحي Azure OCR وOpenAI

المفاتيح تُوضع في ملف `.env` في جذر المشروع فقط، بجانب `.env.example`. الملف مستثنى في `.gitignore`، فلا يدخل Git. لا تلصق المفاتيح في الشات أو في أي ملف آخر. كل القيم أدناه وهمية.

## 1. Azure Document Intelligence (قراءة الصور)

1. ادخل [portal.azure.com](https://portal.azure.com) بحسابك.
2. من الشريط العلوي ابحث عن **Document Intelligence** (الاسم القديم Form Recognizer)، ثم **Create**. إذا ظهر لك **Azure AI services multi-service account** فتجاهله واختر Document Intelligence نفسه.
3. صفحة **Basics**:
   - **Subscription:** اشتراكك (حساب الطالب Azure for Students يكفي).
   - **Resource group:** **Create new**، مثل `rg-sawn-demo`.
   - **Region:** منطقة قريبة تدعم Document Intelligence، مثل `West Europe` أو `Sweden Central` أو `UAE North`.
   - **Name:** اسم فريد يصير جزءًا من الرابط، مثل `sawn-ocr-demo`.
   - **Pricing tier:** `Free F0` للتجربة: 500 صفحة شهريًا، وأول صفحتين فقط من كل ملف، وهذا يكفي للقطات الإعلانات. `Standard S0` مدفوع، ولا تحتاجه الآن.
4. **Network:** اترك **All networks** للتجربة.
5. **Identity** و**Tags:** اتركها كما هي، ثم **Review + create** ثم **Create**. انتظر حتى تظهر رسالة اكتمال النشر، ثم **Go to resource**.
6. من القائمة الجانبية للمورد: **Resource Management** ثم **Keys and Endpoint**:
   - **KEY 1:** هذا `AZURE_DI_KEY`. يكفي مفتاح واحد، والثاني احتياطي للتدوير.
   - **Endpoint:** هذا `AZURE_DI_ENDPOINT`، وشكله `https://sawn-ocr-demo.cognitiveservices.azure.com/`.

الخدمة تستدعي `prebuilt-read` عبر REST بنسخة `2024-11-30` (`app/readers/image_reader.py`)، فلا تحتاج مكتبة Azure.

## 2. OpenAI (الاستخراج بـGPT)

1. ادخل [platform.openai.com](https://platform.openai.com). هذا الحساب منفصل عن اشتراك ChatGPT.
2. **Settings** ثم **Billing:** أضف رصيدًا. الـAPI لا يعمل بدون رصيد حتى لو كان عندك ChatGPT Plus.
3. **API keys** (من [platform.openai.com/api-keys](https://platform.openai.com/api-keys)) ثم **Create new secret key**. سمّه `sawn-local`، واختر المشروع الافتراضي، والصلاحيات **All**، أو **Restricted** مع تفعيل **Responses** للكتابة.
4. انسخ المفتاح فورًا، فلن يظهر مرة ثانية. شكله `sk-proj-...`.

## 3. ملف `.env`

```powershell
copy .env.example .env
notepad .env
```

عدّل هذه الأسطر فقط. القيم هنا وهمية:

```dotenv
AZURE_DI_ENDPOINT=https://sawn-ocr-demo.cognitiveservices.azure.com/
AZURE_DI_KEY=0000000000000000000000000000000a
AZURE_DI_API_VERSION=2024-11-30
OCR_PROVIDER=azure

EXTRACTOR=rules
OPENAI_API_KEY=sk-proj-EXAMPLE-NOT-A-REAL-KEY
OPENAI_MODEL=gpt-5-mini
OPENAI_REASONING_EFFORT=low
```

| المتغير | يقرؤه | ملاحظة |
|---|---|---|
| `AZURE_DI_ENDPOINT` | `app/config.py` → `image_reader.py` | من صفحة Keys and Endpoint |
| `AZURE_DI_KEY` | نفسه | KEY 1 |
| `AZURE_DI_API_VERSION` | نفسه | اتركه `2024-11-30` |
| `OCR_PROVIDER` | نفسه | `azure` لقراءة صور جديدة، و`fixture` للقراءات المحفوظة في `samples/ocr` فقط |
| `EXTRACTOR` | `app/main.py` | المحرك الافتراضي: `rules` أو `gpt`. الطلب يقدر يحدده بحقل `extractor`. |
| `OPENAI_API_KEY` | `app/extraction/llm.py` | |
| `OPENAI_MODEL` | نفسه | الافتراضي `gpt-5-mini`، ويمكن تغييره إلى أي نموذج يدعم Structured Outputs |
| `OPENAI_REASONING_EFFORT` | نفسه | `minimal` أو `low` أو `medium` أو `high`. اتركه فارغًا لنموذج لا يدعم الاستدلال، مثل `gpt-4.1-mini`. |

- **متغيرات البيئة تتقدم على `.env`.** إذا سبق وضبطت `OCR_PROVIDER=fixture` في نافذة PowerShell، احذفه بـ`Remove-Item Env:OCR_PROVIDER` أو افتح نافذة جديدة.
- **الخدمة تقرأ `.env` عند التشغيل فقط**، فأعد تشغيل uvicorn بعد أي تعديل.
- **مع `OCR_PROVIDER=azure` كل صورة تُرسل إلى Azure،** حتى صور `samples/images`.

## 4. التحقق ثم استئناف الاختبارات

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python scripts\demo_site.py
.\.venv\Scripts\python scripts\ocr_preview.py samples\images\ad05_nakheel.png
.\.venv\Scripts\python scripts\run_team_cases.py --extractor gpt   --input text
.\.venv\Scripts\python scripts\run_team_cases.py --extractor rules --input image
.\.venv\Scripts\python scripts\run_team_cases.py --extractor gpt   --input image
```

- الأمران الأولان في نافذتين منفصلتين.
- `run_team_cases.py` يحتاج حزمة الفريق في `team_data/` وصورها، وهي غير منشورة في المستودع (راجع README).
- `ocr_preview.py` يطبع النص الذي قرأه Azure سطرًا سطرًا مع أدنى ثقة لكل سطر.
- `/health` يجب أن يُظهر `"azure_configured": true` و`"openai_configured": true`.
- النتائج تُكتب في `reports/team_cases/`، والجدول المقارن في `reports/team_cases/comparison.md`.

إذا ظهر خطأ:

- `ocr_failed` مع HTTP 401: المفتاح أو الـEndpoint خطأ، أو المورد في منطقة أخرى.
- `llm_failed` مع HTTP 401: مفتاح OpenAI خطأ.
- `llm_failed` مع HTTP 429: لا يوجد رصيد، أو تجاوزت حد الطلبات.
