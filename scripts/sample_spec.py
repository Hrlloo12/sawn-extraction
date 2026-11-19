import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
FORMATS = ("text", "image", "url")
DEMO_BASE = "http://127.0.0.1:8100"

PLATFORM_NAME = "سوق إعلانات الاستقدام التجريبي"
PLATFORM_HEADER_SUPPORT = "الدعم الفني: 920000111"
PLATFORM_FOOTER_LINES = ["خدمة العملاء 800 111 0000", "للتواصل مع المنصة: support@souq-demo.example"]
PLATFORM_SOCIAL = "https://instagram.com/souq_demo_platform"
STANDARD_PLATFORM = [
    {"type": "toll_free", "value": "+966920000111"},
    {"type": "toll_free", "value": "+9668001110000"},
    {"type": "email", "value": "support@souq-demo.example"},
    {"type": "social", "value": "@souq_demo_platform", "platform": "instagram"},
]


def load_ads() -> list[dict]:
    return json.loads((SAMPLES / "ads.json").read_text(encoding="utf-8"))


def expected_for(ad: dict, fmt: str) -> dict:
    base = ad["expected"]["text"]
    override = ad["expected"].get(fmt, {})
    result = {k: v for k, v in base.items()}
    for key, value in override.items():
        if key != "add_contacts":
            result[key] = value
    if "add_contacts" in override:
        result["contacts"] = list(result["contacts"]) + override["add_contacts"]
    if result.get("platform_contacts") == "standard":
        result["platform_contacts"] = STANDARD_PLATFORM
    return result


def page_url(ad: dict, base: str = DEMO_BASE) -> str:
    return f"{base}/ads/{ad['id']}.html"


def contact_key(item: dict) -> tuple:
    return item["type"], item.get("value", item.get("normalized_value"))
