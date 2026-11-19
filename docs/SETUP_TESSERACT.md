# تشغيل قراءة الصور بـTesseract (بدون أي حساب سحابي)

Tesseract يقرأ الصور محليًا على الجهاز، بدون مفتاح، وبدون أن تخرج الصورة لأي جهة.

## 1. التثبيت (Windows، مرة واحدة)

```powershell
winget install --id UB-Mannheim.TesseractOCR -e
```

إذا ظهرت نافذة صلاحيات Windows (UAC) اضغط **Yes**. يُثبَّت في `C:\Program Files\Tesseract-OCR\` ومعه الإنجليزي فقط.

## 2. حزمة العربي

ننزّلها في مجلد المستخدم، فلا تحتاج صلاحيات مدير:

```powershell
mkdir $HOME\tessdata
curl.exe -L -o $HOME\tessdata\ara.traineddata https://github.com/tesseract-ocr/tessdata_best/raw/4.1.0/ara.traineddata
copy "C:\Program Files\Tesseract-OCR\tessdata\eng.traineddata" $HOME\tessdata\
copy "C:\Program Files\Tesseract-OCR\tessdata\osd.traineddata" $HOME\tessdata\
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --tessdata-dir $HOME\tessdata --list-langs
```

آخر أمر يجب أن يطبع `ara` و`eng` و`osd`.

macOS أو Linux:

```bash
brew install tesseract tesseract-lang
sudo apt install tesseract-ocr tesseract-ocr-ara
```

مع هذا التثبيت يمكن ترك `TESSERACT_TESSDATA_DIR` فارغًا.

## 3. ملف `.env`

```dotenv
OCR_PROVIDER=tesseract
TESSERACT_TESSDATA_DIR=C:/Users/<اسمك>/tessdata
TESSERACT_LANGS=ara+eng
```

- `TESSERACT_CMD`: يُترك فارغًا. الخدمة تبحث في `PATH` ثم في `C:\Program Files\Tesseract-OCR\tesseract.exe`.
- `TESSERACT_JOIN_WRAPPED=true` (الافتراضي): يدمج السطور الملتفّة في الصورة، فاسم المكتب أو الآيبان المكسور على سطرين يُقرأ كاملًا.

## 4. التشغيل والتجربة

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- `/health` يجب أن يُظهر `"ocr_provider": "tesseract"`.
- من `http://127.0.0.1:8000/docs` اختر `POST /extract`، و`input_type=image`، وارفع صورة من `samples/team_images/`.

من سطر الأوامر:

```powershell
curl.exe -F input_type=image -F file=@samples/team_images/T07.png http://127.0.0.1:8000/extract
.\.venv\Scripts\python scripts\run_team_cases.py --extractor rules --input image
```

الأمر الثاني يشغّل الصور العشرين على الخدمة ثم المطابِق، ويكتب `reports/team_cases/rules_image.md`. يحتاج `requirements.txt` المحدّث، لأن Pillow صار ضمن التشغيل.

## كيف يتعامل مع أخطاء القراءة

1. يقرأ الصورة بـ`ara+eng`، ثم يعيد ترتيب الكلمات حسب موضعها، لأن Tesseract يقلب ترتيب مجموعات الأرقام داخل السطر العربي.
2. يعيد قراءة كل رقم أو رابط أو مجموعة لاتينية داخل سطر عربي بنموذج الإنجليزي وحده.
   - إذا اتفقت القراءتان تبقى الثقة الأصلية. تحوّل الأرقام إلى حروف فقط، مثل `5869` إلى `SA69`، يُعتبر تصحيحًا.
   - إذا اختلفتا في الأرقام تنخفض الثقة إلى 0.3، فتصير القيمة الموحّدة `null` مع تنبيه `ambiguous_phone` أو `ambiguous_iban` أو `ambiguous_link`، ولا تدخل المطابقة.
3. الرقم أو الآيبان أو الرابط الذي تقل ثقته عن `OCR_MIN_DIGIT_CONFIDENCE` (0.6) لا يُوحَّد.
4. سطر في الصورة يشبه آيبانًا ببادئة مقروءة خطأ (مثل `5869 0088 …`) يُرجع `ambiguous_iban` مع الدليل وبدون قيمة.
