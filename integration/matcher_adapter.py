import json
import re
import sys
from pathlib import Path

PHONE_TYPES = {"mobile", "landline", "toll_free", "whatsapp"}
MATCHER_FIELDS = (
    "office_name",
    "phones",
    "urls",
    "ibans",
    "account_holder",
    "personal_account",
    "new_account_claim",
    "urgency_phrases",
)
EXTRA_FIELDS = ("payment_request", "ocr_urls", "unconfirmed")


def _unique(values) -> list:
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def domain_only(value: str) -> str:
    host = re.sub(r"^[a-z]+://", "", value.strip().lower())
    host = re.split(r"[/?#]", host, maxsplit=1)[0].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _normalized(field: dict | None) -> str:
    return (field or {}).get("normalized_value") or ""


def _flag(payment: dict, name: str) -> bool:
    return bool((payment.get(name) or {}).get("value", False))


def to_matcher_input(response: dict, include_unclear: bool = True) -> dict:
    allowed = {"advertiser", "unclear"} if include_unclear else {"advertiser"}
    contacts = [
        c for c in response.get("contacts", []) if c.get("attribution") in allowed and c.get("normalized_value")
    ]
    payment = response.get("payment") or {}
    return {
        "office_name": _normalized(response.get("office_name")),
        "phones": _unique(c["normalized_value"].lstrip("+") for c in contacts if c["type"] in PHONE_TYPES),
        "urls": _unique(domain_only(c["normalized_value"]) for c in contacts if c["type"] == "website"),
        "ibans": _unique(i.get("normalized_value") for i in response.get("ibans", [])),
        "account_holder": _normalized(response.get("account_holder")),
        "personal_account": _flag(payment, "personal_account"),
        "new_account_claim": _flag(payment, "new_account_claim"),
        "urgency_phrases": _unique(p.get("text") for p in payment.get("urgency_phrases", [])),
        "payment_request": _flag(payment, "payment_request"),
        "ocr_urls": _unique(
            domain_only(c["normalized_value"]) for c in contacts if c["type"] == "website" and c.get("source") == "ocr"
        ),
        "unconfirmed": [
            {"type": c["type"], "raw": c.get("raw_value", "")}
            for c in response.get("contacts", [])
            if c.get("attribution") in allowed and not c.get("normalized_value") and c["type"] != "email"
        ]
        + [{"type": "iban", "raw": i.get("raw_value", "")} for i in response.get("ibans", []) if not i.get("normalized_value")],
    }


def main():
    response = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(to_matcher_input(response), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
