import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

from scripts.build_samples import LINE_HEIGHT, MARGIN, WIDTH, font, visual

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "team_data" / "test_cases.json"
OUT = ROOT / "samples" / "team_images"


def wrap(text: str, draw: ImageDraw.ImageDraw, fnt) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        left, _, right, _ = draw.textbbox((0, 0), visual(trial), font=fnt)
        if current and right - left > WIDTH - 2 * MARGIN:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def render(case: dict, fnt) -> Path:
    probe = ImageDraw.Draw(Image.new("RGB", (WIDTH, 10)))
    lines = wrap(case["ad_text"], probe, fnt)
    image = Image.new("RGB", (WIDTH, MARGIN * 2 + LINE_HEIGHT * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        shown = visual(line)
        left, _, right, _ = draw.textbbox((0, 0), shown, font=fnt)
        draw.text((WIDTH - MARGIN - (right - left) - left, MARGIN + i * LINE_HEIGHT), shown, font=fnt, fill=(25, 25, 25))
    path = OUT / f"{case['id']}.png"
    image.save(path, optimize=True)
    return path


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fnt = font()
    for case in json.loads(CASES.read_text(encoding="utf-8"))["cases"]:
        print(render(case, fnt).relative_to(ROOT))


if __name__ == "__main__":
    main()
