import hashlib
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import arabic_reshaper
from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    from bidi import get_display
except ImportError:
    from bidi.algorithm import get_display

from scripts.sample_spec import (
    PLATFORM_FOOTER_LINES,
    PLATFORM_HEADER_SUPPORT,
    PLATFORM_NAME,
    PLATFORM_SOCIAL,
    SAMPLES,
    load_ads,
)

FONT_CANDIDATES = ["C:/Windows/Fonts/tahoma.ttf", "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
WIDTH = 1000
MARGIN = 48
LINE_HEIGHT = 64
FONT_SIZE = 34
SYNTHETIC_NOTE = "Simulated prebuilt-read output generated from the sample text; not a recorded Azure response."


def font() -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, FONT_SIZE)
    raise SystemExit("No TrueType font with Arabic glyphs found")


def visual(text: str) -> str:
    return get_display(arabic_reshaper.reshape(text))


def build_text(ad: dict):
    (SAMPLES / "text" / f"{ad['id']}.txt").write_text("\n".join(ad["text"]) + "\n", encoding="utf-8")


def build_page(ad: dict):
    page = ad["page"]
    moved = set(page.get("move_to_footer", []))
    body_lines = [line for i, line in enumerate(ad["text"]) if i not in moved]
    footer_extra = [ad["text"][i] for i in sorted(moved)]
    paragraphs = "\n".join(f"        <p>{html.escape(line)}</p>" for line in body_lines)
    buttons = "\n".join(
        f'        <a class="btn" href="{html.escape(b["href"])}">{html.escape(b["label"])}</a>' for b in page["buttons"]
    )
    title = html.escape(ad["title"])
    description = html.escape(" - ".join(body_lines[1:3]))
    advertiser = (
        f'      <div class="advertiser-box">المعلن: {html.escape(page["advertiser_box"])}</div>\n'
        if page.get("advertiser_box")
        else ""
    )
    if page["layout"] == "flat":
        support = "\n".join(
            f"    <div>{html.escape(line)}</div>" for line in [PLATFORM_HEADER_SUPPORT, PLATFORM_FOOTER_LINES[0], *footer_extra]
        )
        document = f"""<!doctype html>
<html lang="ar" dir="rtl">
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
  <div class="c1"><div class="c2">{PLATFORM_NAME}</div></div>
  <div class="c3">
    <div class="c4">{title}</div>
{paragraphs.replace("<p>", "<div>").replace("</p>", "</div>")}
{buttons}
  </div>
  <div class="c5">
{support}
  </div>
</body>
</html>
"""
    else:
        footer_lines = "\n".join(f"    <p>{html.escape(line)}</p>" for line in [*PLATFORM_FOOTER_LINES, *footer_extra])
        document = f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <title>{title} | {PLATFORM_NAME}</title>
  <meta name="description" content="{description}">
</head>
<body>
  <header class="site-header">
    <div class="logo">{PLATFORM_NAME}</div>
    <nav><a href="/">الرئيسية</a> <a href="/ads/">الإعلانات</a></nav>
    <div class="support">{PLATFORM_HEADER_SUPPORT}</div>
  </header>
  <main>
    <article class="ad-details">
      <h1>{title}</h1>
      <section class="description">
{paragraphs}
      </section>
{advertiser}      <div class="contact-buttons">
{buttons}
      </div>
      <a class="report" href="/report/{ad['id']}">الإبلاغ عن الإعلان</a>
    </article>
  </main>
  <footer>
{footer_lines}
    <a href="{PLATFORM_SOCIAL}">انستقرام المنصة</a>
  </footer>
</body>
</html>
"""
    (SAMPLES / "pages" / f"{ad['id']}.html").write_text(document, encoding="utf-8")


def _polygon(x0, y0, x1, y1):
    return [float(v) for v in (x0, y0, x1, y0, x1, y1, x0, y1)]


def build_image(ad: dict):
    spec = ad.get("image", {})
    fnt = font()
    lines = ad["text"]
    height = MARGIN * 2 + LINE_HEIGHT * len(lines)
    image = Image.new("RGB", (WIDTH, height), "white")
    draw = ImageDraw.Draw(image)
    boxes = []
    for i, line in enumerate(lines):
        shown = visual(line)
        left, top, right, bottom = draw.textbbox((0, 0), shown, font=fnt)
        width = right - left
        x = WIDTH - MARGIN - width
        y = MARGIN + i * LINE_HEIGHT
        draw.text((x - left, y), shown, font=fnt, fill=(25, 25, 25))
        boxes.append((x, y + top, x + width, y + bottom))
    if spec.get("variant") == "blurry":
        small = image.resize((WIDTH // 3, height // 3), Image.BILINEAR)
        image = small.resize((WIDTH, height), Image.BILINEAR).filter(ImageFilter.GaussianBlur(2.4))
    path = SAMPLES / "images" / f"{ad['id']}.png"
    image.save(path, optimize=True)
    data = path.read_bytes()

    overrides = {o["line"]: o for o in spec.get("ocr_overrides", [])}
    splits = {s["line"]: s["after"] for s in spec.get("split", [])}
    ocr_lines = []
    for i, line in enumerate(lines):
        override = overrides.get(i, {})
        content = override.get("content", line)
        low = override.get("low_confidence_words", {})
        x0, y0, x1, y1 = boxes[i]
        if i in splits and content.startswith(splits[i]):
            head = splits[i]
            tail = content[len(head) :].strip()
            cut = x1 - (x1 - x0) * len(head) / max(len(content), 1)
            ocr_lines.append((head, _polygon(cut, y0, x1, y1), low))
            ocr_lines.append((tail, _polygon(x0, y0, cut - 12, y1), low))
        else:
            ocr_lines.append((content, _polygon(x0, y0, x1, y1), low))

    content_parts = []
    page_lines = []
    words = []
    offset = 0
    for text, polygon, low in ocr_lines:
        page_lines.append({"content": text, "polygon": polygon, "spans": [{"offset": offset, "length": len(text)}]})
        cursor = 0
        for word in text.split(" "):
            if word:
                start = text.index(word, cursor)
                default = 0.97 if spec.get("variant") != "blurry" else 0.86
                words.append(
                    {
                        "content": word,
                        "polygon": polygon,
                        "confidence": low.get(word, default),
                        "span": {"offset": offset + start, "length": len(word)},
                    }
                )
                cursor = start + len(word)
        content_parts.append(text)
        offset += len(text) + 1
    result = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "image": path.name,
        "synthetic": True,
        "note": SYNTHETIC_NOTE,
        "analyzeResult": {
            "apiVersion": "2024-11-30",
            "modelId": "prebuilt-read",
            "content": "\n".join(content_parts),
            "pages": [
                {
                    "pageNumber": 1,
                    "width": WIDTH,
                    "height": height,
                    "unit": "pixel",
                    "lines": page_lines,
                    "words": words,
                }
            ],
        },
    }
    (SAMPLES / "ocr" / f"{ad['id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def build_blank_image():
    image = Image.new("RGB", (800, 400), (236, 236, 236))
    draw = ImageDraw.Draw(image)
    for k in range(0, 800, 16):
        draw.line([(k, 0), (800 - k, 400)], fill=(200, 200, 200), width=3)
    path = SAMPLES / "images" / "blank_pattern.png"
    image.save(path, optimize=True)
    result = {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "image": path.name,
        "synthetic": True,
        "note": SYNTHETIC_NOTE,
        "analyzeResult": {
            "apiVersion": "2024-11-30",
            "modelId": "prebuilt-read",
            "content": "",
            "pages": [{"pageNumber": 1, "width": 800, "height": 400, "unit": "pixel", "lines": [], "words": []}],
        },
    }
    (SAMPLES / "ocr" / "blank_pattern.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    for folder in ("text", "pages", "images", "ocr"):
        (SAMPLES / folder).mkdir(parents=True, exist_ok=True)
    ads = load_ads()
    for ad in ads:
        build_text(ad)
        build_page(ad)
        build_image(ad)
    build_blank_image()
    print(f"built {len(ads)} ads x 3 formats into {SAMPLES}")


if __name__ == "__main__":
    main()
