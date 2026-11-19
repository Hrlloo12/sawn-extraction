from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import ReaderDeps, app, get_reader_deps
from scripts.demo_site import app as demo_app

ROOT = Path(__file__).resolve().parents[1]
HAS_TEAM_DATA = (ROOT / "team_data" / "matcher.py").exists()
HAS_TEAM_IMAGES = (ROOT / "samples" / "team_images" / "T01.png").exists()

collect_ignore = [] if HAS_TEAM_DATA else ["test_demo_guards.py"]


def pytest_collection_modifyitems(config, items):
    missing = {
        "team_data": (HAS_TEAM_DATA, "حزمة الفريق team_data/ غير موجودة (راجع README)"),
        "team_images": (HAS_TEAM_IMAGES, "صور الفريق samples/team_images/ غير موجودة (راجع README)"),
    }
    for item in items:
        for marker, (present, reason) in missing.items():
            if not present and item.get_closest_marker(marker):
                item.add_marker(pytest.mark.skip(reason=reason))

FAKE_DNS = {
    "localhost": ["127.0.0.1"],
    "127.0.0.1": ["127.0.0.1"],
    "ads.sawn.test": ["93.184.215.14"],
    "internal.sawn.test": ["10.0.0.5"],
    "mapped.sawn.test": ["::ffff:192.168.1.10"],
}


async def fake_resolver(host: str, port: int) -> list[str]:
    if host not in FAKE_DNS:
        raise OSError("unknown host")
    return FAKE_DNS[host]


def make_settings(**overrides) -> Settings:
    values = {
        "ocr_provider": "fixture",
        "allowed_domains": "localhost,127.0.0.1,ads.sawn.test,internal.sawn.test,mapped.sawn.test",
        "allow_localhost": True,
        "fetch_timeout_seconds": 2,
        "azure_di_endpoint": "",
        "azure_di_key": "",
        "extractor": "rules",
        "image_domain_reader": "ocr",
        "openai_api_key": "",
        "openai_model": "gpt-5-mini",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def demo_transport() -> httpx.AsyncBaseTransport:
    return httpx.ASGITransport(app=demo_app)


@pytest.fixture
def client(settings, demo_transport):
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_reader_deps] = lambda: ReaderDeps(http_transport=demo_transport, resolver=fake_resolver)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
