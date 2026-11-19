import asyncio
import csv
import io
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

from app.config import Settings
from app.readers.base import ReadError

ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
DIRECTION_MARKS = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"))
LATIN = re.compile(r"[A-Za-z]")
DIGIT = re.compile(r"\d")
LATIN_LANGS = "eng"
ARABIC_LANGS = "ara"
LOW_LATIN_CONF = 80
REREAD_SCALE = 2
CHANNEL_LIKE = re.compile(r"\d|[A-Za-z0-9][\w\-]*\.[A-Za-z]{2,}|@")
LEADING_PUNCT = re.compile(r"^[.,،؛;:!?؟]+(?=\w)")
SENTENCE_END = re.compile(r"[.!؟?:：]\s*$")
WRAP_TOLERANCE = 0.08
DISAGREE_CONF = 30.0
REREAD_PADDING = 6
SPARSE_SCALE = 2
SPARSE_PSM = 11
LOCAL_THRESHOLD = 2
EXTRA_MIN_CONF = 80
GOOD_CONF = 80
WINDOWS_DEFAULT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def find_tesseract(configured: str) -> str | None:
    if configured:
        return configured if Path(configured).exists() or shutil.which(configured) else None
    found = shutil.which("tesseract")
    if found:
        return found
    return str(WINDOWS_DEFAULT) if WINDOWS_DEFAULT.exists() else None


def parse_tsv(tsv: str) -> list[list[dict]]:
    lines: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").translate(DIRECTION_MARKS).strip()
        if not text:
            continue
        key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        if key not in lines:
            lines[key] = []
            order.append(key)
        left, top, width, height = (int(row[k]) for k in ("left", "top", "width", "height"))
        conf = float(row.get("conf") or -1)
        lines[key].append(
            {"text": text, "left": left, "top": top, "right": left + width, "bottom": top + height, "conf": conf}
        )
    return [logical_order(lines[key]) for key in order]


def suspect_arabic(word: dict) -> bool:
    return bool(LATIN.search(word["text"])) and not DIGIT.search(word["text"]) and word["conf"] < LOW_LATIN_CONF


def _alnum(words: list[dict]) -> str:
    return "".join(ch for w in words for ch in w["text"] if ch.isalnum()).lower()


def reads_agree(first: list[dict], second: list[dict]) -> bool:
    a, b = _alnum(first), _alnum(second)
    if len(a) != len(b):
        return False
    return all(x == y or (x.isdigit() and y.isalpha()) for x, y in zip(a, b))


def channel_like(word: dict) -> bool:
    return bool(CHANNEL_LIKE.search(word["text"]))


def move_leading_punctuation(word: dict) -> dict:
    m = LEADING_PUNCT.match(word["text"])
    if m:
        word["text"] = word["text"][m.end() :] + m.group()
    return word


def is_wrapped(words: list[dict], next_words: list[dict], block_left: int, block_right: int) -> bool:
    text = " ".join(w["text"] for w in words)
    if SENTENCE_END.search(text):
        return False
    width = max(block_right - block_left, 1)
    rtl = any(ARABIC.search(w["text"]) for w in words)
    edge = min(w["left"] for w in words) - block_left if rtl else block_right - max(w["right"] for w in words)
    if edge > WRAP_TOLERANCE * width:
        return False
    rtl_next = any(ARABIC.search(w["text"]) for w in next_words)
    return rtl == rtl_next


def join_wrapped(lines: list[list[dict]]) -> list[list[dict]]:
    if len(lines) < 2:
        return lines
    block_left = min(w["left"] for line in lines for w in line)
    block_right = max(w["right"] for line in lines for w in line)
    joined = [list(lines[0])]
    for prev, line in zip(lines, lines[1:]):
        if is_wrapped(prev, line, block_left, block_right):
            joined[-1].extend(line)
        else:
            joined.append(list(line))
    return joined


def is_rtl(word: dict) -> bool:
    return bool(ARABIC.search(word["text"])) or suspect_arabic(word)


def logical_order(words: list[dict]) -> list[dict]:
    if not any(ARABIC.search(w["text"]) for w in words):
        return sorted(words, key=lambda w: w["left"])
    visual = sorted(words, key=lambda w: -(w["left"] + w["right"]))
    result: list[dict] = []
    run: list[dict] = []
    for word in visual:
        if is_rtl(word):
            result.extend(reversed(run))
            run = []
            result.append(word)
        else:
            run.append(move_leading_punctuation(word))
    result.extend(reversed(run))
    return result


def confident_chars(lines: list[list[dict]]) -> int:
    return sum(len(_alnum([w])) for line in lines for w in line if w["conf"] >= GOOD_CONF)


def novel_lines(base: list[list[dict]], candidates: list[list[dict]]) -> list[list[dict]]:
    known = "".join(_alnum(line) for line in base)
    added = []
    for line in candidates:
        kept = [w for w in line if w["conf"] >= EXTRA_MIN_CONF or channel_like(w)]
        key = _alnum(kept)
        if len(key) < 4 or key in known:
            continue
        added.append(kept)
        known += key
    return added


def to_analyze_result(lines: list[list[dict]]) -> dict:
    content_parts: list[str] = []
    page_lines, page_words = [], []
    offset = 0
    for words in lines:
        line_start = offset
        texts = []
        for i, word in enumerate(words):
            if i:
                offset += 1
            confidence = round(word["conf"] / 100, 3) if word["conf"] >= 0 else None
            page_words.append(
                {
                    "content": word["text"],
                    "confidence": confidence,
                    "span": {"offset": offset, "length": len(word["text"])},
                    "polygon": _box(word["left"], word["top"], word["right"], word["bottom"]),
                }
            )
            offset += len(word["text"])
            texts.append(word["text"])
        text = " ".join(texts)
        page_lines.append(
            {
                "content": text,
                "polygon": _box(
                    min(w["left"] for w in words),
                    min(w["top"] for w in words),
                    max(w["right"] for w in words),
                    max(w["bottom"] for w in words),
                ),
                "spans": [{"offset": line_start, "length": len(text)}],
            }
        )
        content_parts.append(text)
        offset += 1
    return {"content": "\n".join(content_parts), "pages": [{"pageNumber": 1, "lines": page_lines, "words": page_words}]}


def _box(x0: int, y0: int, x1: int, y1: int) -> list[float]:
    return [float(v) for v in (x0, y0, x1, y0, x1, y1, x0, y1)]


class TesseractProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def command(self, executable: str, langs: str | None = None, psm: int | None = None, threshold: int = 0) -> list[str]:
        s = self.settings
        cmd = [executable, "stdin", "stdout", "-l", langs or s.tesseract_langs, "--psm", str(psm or s.tesseract_psm)]
        if s.tesseract_tessdata_dir:
            cmd += ["--tessdata-dir", s.tesseract_tessdata_dir]
        if threshold:
            cmd += ["-c", f"thresholding_method={threshold}"]
        return cmd + ["-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]

    def run(self, data: bytes, langs: str | None = None, psm: int | None = None, threshold: int = 0) -> str:
        executable = find_tesseract(self.settings.tesseract_cmd)
        if executable is None:
            raise ReadError("ocr_not_configured", "Tesseract غير مثبت أو TESSERACT_CMD غير صحيح", "paste_text")
        try:
            done = subprocess.run(
                self.command(executable, langs, psm, threshold),
                input=data,
                capture_output=True,
                timeout=self.settings.ocr_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReadError("timeout", "انتهت مهلة قراءة الصورة", "paste_text") from exc
        except OSError as exc:
            raise ReadError("ocr_failed", "تعذّر تشغيل Tesseract", "paste_text") from exc
        stderr = done.stderr.decode("utf-8", "replace")
        if "Failed loading language" in stderr or "Error opening data file" in stderr:
            raise ReadError(
                "ocr_not_configured", f"حزمة لغة Tesseract غير موجودة ({langs or self.settings.tesseract_langs})", "paste_text"
            )
        if done.returncode != 0:
            raise ReadError("ocr_failed", "فشلت قراءة الصورة في Tesseract", "paste_text")
        return done.stdout.decode("utf-8", "replace")

    def reread(self, image, words: list[dict], langs: str, psm: int) -> list[dict] | None:
        pad = REREAD_PADDING
        x0 = max(0, min(w["left"] for w in words) - pad)
        y0 = max(0, min(w["top"] for w in words) - pad)
        x1 = min(image.width, max(w["right"] for w in words) + pad)
        y1 = min(image.height, max(w["bottom"] for w in words) + pad)
        crop = image.crop((x0, y0, x1, y1))
        crop = crop.resize((crop.width * REREAD_SCALE, crop.height * REREAD_SCALE), Image.LANCZOS)
        buffer = io.BytesIO()
        crop.save(buffer, "PNG")
        found = [w for line in parse_tsv(self.run(buffer.getvalue(), langs, psm)) for w in line]
        if not found:
            return None
        box = {"left": x0 + pad, "top": y0 + pad, "right": x1 - pad, "bottom": y1 - pad}
        return [box | {"text": w["text"], "conf": w["conf"]} for w in found]

    def confirm(self, image, words: list[dict]) -> list[dict] | None:
        again = self.reread(image, words, LATIN_LANGS, 7)
        if not again:
            return None
        if len(again) == len(words):
            for old, new in zip(words, again):
                if _alnum([old]) == _alnum([new]):
                    new["conf"] = old["conf"]
                elif not reads_agree([old], [new]):
                    new["conf"] = min(new["conf"], old["conf"], DISAGREE_CONF)
            return again
        first = min(w["conf"] for w in words)
        agree = reads_agree(words, again)
        for w in again:
            w["conf"] = first if agree else min(w["conf"], first, DISAGREE_CONF)
        return again

    def refine(self, image, lines: list[list[dict]]) -> list[list[dict]]:
        refined = []
        for words in lines:
            if not any(ARABIC.search(w["text"]) for w in words):
                line = []
                for word in words:
                    line.extend((self.confirm(image, [word]) or [word]) if channel_like(word) else [word])
                refined.append(line)
                continue
            out: list[dict] = []
            run: list[dict] = []

            def flush():
                if not run:
                    return
                replacement = None
                if len(run) > 1 or any(channel_like(w) for w in run):
                    replacement = self.confirm(image, run)
                out.extend(move_leading_punctuation(w) for w in (replacement or run))
                run.clear()

            for word in words:
                if ARABIC.search(word["text"]):
                    flush()
                    out.append(word)
                elif suspect_arabic(word):
                    flush()
                    again = self.reread(image, [word], ARABIC_LANGS, 8)
                    out.extend(again if again and any(ARABIC.search(w["text"]) for w in again) else [word])
                else:
                    run.append(word)
            flush()
            refined.append(out)
        return refined

    def sparse_pass(self, image, threshold: int = 0) -> list[list[dict]]:
        gray = ImageOps.grayscale(image)
        gray = gray.resize((gray.width * SPARSE_SCALE, gray.height * SPARSE_SCALE), Image.LANCZOS)
        buffer = io.BytesIO()
        gray.save(buffer, "PNG")
        lines = parse_tsv(self.run(buffer.getvalue(), psm=SPARSE_PSM, threshold=threshold))
        for line in lines:
            for w in line:
                for key in ("left", "right", "top", "bottom"):
                    w[key] = w[key] // SPARSE_SCALE
        return lines

    def read(self, data: bytes) -> dict:
        try:
            image = Image.open(io.BytesIO(data)).convert("RGB")
        except (OSError, ValueError):
            image = None
        if image is None or not self.settings.tesseract_multipass:
            lines = parse_tsv(self.run(data))
            if image is not None:
                lines = self.refine(image, lines)
            return to_analyze_result(join_wrapped(lines) if self.settings.tesseract_join_wrapped else lines)
        with ThreadPoolExecutor(max_workers=3) as pool:
            primary = pool.submit(lambda: parse_tsv(self.run(data)))
            sparse = pool.submit(self.sparse_pass, image)
            local = pool.submit(self.sparse_pass, image, LOCAL_THRESHOLD)
            primary_lines = self.refine(image, primary.result())
            sparse_lines = self.refine(image, sparse.result())
            local_lines = local.result()
        if self.settings.tesseract_join_wrapped:
            primary_lines = join_wrapped(primary_lines)
        base, other = (primary_lines, sparse_lines)
        if confident_chars(sparse_lines) > confident_chars(primary_lines):
            base, other = sparse_lines, primary_lines
        return to_analyze_result(base + novel_lines(base, other + local_lines))

    async def analyze(self, data: bytes) -> dict:
        return await asyncio.to_thread(self.read, data)
