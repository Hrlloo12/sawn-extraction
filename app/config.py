from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _real(value: str) -> bool:
    return bool(value) and not value.startswith("replace-with")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    azure_di_endpoint: str = ""
    azure_di_key: str = ""
    azure_di_api_version: str = "2024-11-30"
    ocr_provider: str = "azure"
    ocr_fixtures_dir: str = "samples/ocr"
    ocr_timeout_seconds: float = 30
    ocr_min_digit_confidence: float = 0.6
    tesseract_cmd: str = ""
    tesseract_tessdata_dir: str = ""
    tesseract_langs: str = "ara+eng"
    tesseract_psm: int = 6
    tesseract_join_wrapped: bool = True
    tesseract_multipass: bool = True

    extractor: str = "gpt"
    image_domain_reader: str = "vision"
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    openai_vision_model: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_reasoning_effort: str = "low"
    openai_timeout_seconds: float = 45

    reference_data_dir: str = "team_data"

    allowed_domains: str = "localhost,127.0.0.1"
    allow_localhost: bool = True
    fetch_timeout_seconds: float = 8
    max_response_bytes: int = 2_000_000
    max_redirects: int = 5

    max_image_bytes: int = 5_000_000
    max_text_chars: int = 20_000
    cors_origins: str = "*"

    @property
    def allowed_domain_list(self) -> list[str]:
        return [d.strip().lower() for d in self.allowed_domains.split(",") if d.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def ocr_fixtures_path(self) -> Path:
        path = Path(self.ocr_fixtures_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def vision_model(self) -> str:
        return self.openai_vision_model or self.openai_model

    @property
    def openai_configured(self) -> bool:
        return _real(self.openai_api_key)

    @property
    def azure_configured(self) -> bool:
        return _real(self.azure_di_endpoint) and _real(self.azure_di_key) and "your-resource-name" not in self.azure_di_endpoint


@lru_cache
def get_settings() -> Settings:
    return Settings()
