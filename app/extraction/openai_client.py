import json
import re

import httpx

from app.config import Settings
from app.readers.http import shared_ssl_context

SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{2,})[A-Za-z0-9_\-*]*")


class LlmError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = redact(message)


def redact(text: str) -> str:
    return SECRET.sub("sk-***", text or "")


async def structured_response(
    settings: Settings,
    model: str,
    instructions: str,
    content,
    schema_name: str,
    schema: dict,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    if not settings.openai_configured:
        raise LlmError("llm_not_configured", "مفتاح OpenAI غير مضبوط في إعدادات الخادم (OPENAI_API_KEY)")
    body = {
        "model": model,
        "input": [{"role": "developer", "content": instructions}, {"role": "user", "content": content}],
        "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
    }
    if settings.openai_reasoning_effort:
        body["reasoning"] = {"effort": settings.openai_reasoning_effort}
    try:
        async with httpx.AsyncClient(
            transport=transport, verify=shared_ssl_context(), timeout=settings.openai_timeout_seconds
        ) as client:
            response = await client.post(
                settings.openai_base_url.rstrip("/") + "/responses",
                json=body,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
    except httpx.TimeoutException as exc:
        raise LlmError("llm_timeout", "انتهت مهلة طلب OpenAI") from exc
    except httpx.HTTPError as exc:
        raise LlmError("llm_failed", f"تعذّر الاتصال بـOpenAI: {type(exc).__name__}") from exc
    if response.status_code != 200:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        raise LlmError("llm_failed", f"OpenAI HTTP {response.status_code}: {detail[:200]}")
    data = response.json()
    if data.get("status") not in (None, "completed"):
        reason = (data.get("incomplete_details") or {}).get("reason", data.get("status"))
        raise LlmError("llm_failed", f"رد OpenAI غير مكتمل: {reason}")
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "refusal":
                raise LlmError("llm_failed", "رفض النموذج الطلب")
            if part.get("type") == "output_text":
                try:
                    return json.loads(part["text"])
                except ValueError as exc:
                    raise LlmError("llm_failed", "رد النموذج ليس JSON صالحًا") from exc
    raise LlmError("llm_failed", "رد OpenAI بلا نص")
