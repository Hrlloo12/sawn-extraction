import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import ReaderDeps, app, get_reader_deps
from scripts.demo_site import app as demo_app
from scripts.sample_spec import DEMO_BASE, ROOT, SAMPLES

EXAMPLES = ROOT / "docs" / "examples"
INTEGRATION = ROOT / "integration" / "extraction_outputs"


async def resolver(host: str, port: int) -> list[str]:
    return ["127.0.0.1"]


def text(ad_id):
    return {"input_type": "text", "value": (SAMPLES / "text" / f"{ad_id}.txt").read_text(encoding="utf-8")}, None


def url(path):
    return {"input_type": "url", "value": DEMO_BASE + path}, None


def image(ad_id):
    return {"input_type": "image"}, ad_id


DOC_CASES = {
    "text_ok": text("ad01_waha"),
    "text_no_office_name": text("ad04_noname"),
    "url_platform_numbers_separated": url("/ads/ad01_waha.html"),
    "url_whatsapp_button_without_visible_number": url("/ads/ad02_sahil.html"),
    "url_attribution_unclear": url("/ads/ad07_jusoor.html"),
    "image_ambiguous_number": image("ad05_nakheel"),
    "url_login_required": url("/private/ad01_waha"),
    "url_fetch_failed": url("/broken"),
}
INTEGRATION_CASES = {
    "ad01_waha_text": text("ad01_waha"),
    "ad01_waha_url": url("/ads/ad01_waha.html"),
    "ad12_waha_fake_url": url("/ads/ad12_waha_fake.html"),
    "ad10_riyada_text": text("ad10_riyada"),
    "ad09_sidra_text": text("ad09_sidra"),
}


def run(client, data, image_id):
    if image_id:
        with open(SAMPLES / "images" / f"{image_id}.png", "rb") as handle:
            return client.post("/extract", data=data, files={"file": (f"{image_id}.png", handle, "image/png")}).json()
    return client.post("/extract", data=data).json()


def main():
    settings = Settings(_env_file=None, ocr_provider="fixture", extractor="rules", image_domain_reader="ocr", allowed_domains="127.0.0.1,localhost")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_reader_deps] = lambda: ReaderDeps(
        http_transport=httpx.ASGITransport(app=demo_app), resolver=resolver
    )
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    INTEGRATION.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        for target, cases in ((EXAMPLES, DOC_CASES), (INTEGRATION, INTEGRATION_CASES)):
            for name, (data, image_id) in cases.items():
                body = run(client, data, image_id)
                body["request_id"] = f"example-{name}"
                prefix = "response_" if target is EXAMPLES else ""
                (target / f"{prefix}{name}.json").write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"{target.name}/{prefix}{name}.json -> {body['status']}")
    app.dependency_overrides.clear()


if __name__ == "__main__":
    main()
