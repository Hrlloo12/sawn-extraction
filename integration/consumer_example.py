import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

SERVICE_URL = "http://127.0.0.1:8000/extract"


@dataclass
class ChannelClaim:
    office_name: str | None
    license_number: str | None
    channel_type: str
    value: str | None
    platform: str | None
    attribution: str
    usable: bool
    evidence: str


def extract_from_service(input_type: str, value: str | None = None, image_path: str | None = None) -> dict:
    if input_type == "image":
        with open(image_path, "rb") as handle:
            response = httpx.post(SERVICE_URL, data={"input_type": "image"}, files={"file": handle}, timeout=30)
    else:
        response = httpx.post(SERVICE_URL, data={"input_type": input_type, "value": value}, timeout=30)
    response.raise_for_status()
    return response.json()


def to_claims(output: dict) -> list[ChannelClaim]:
    office = output["office_name"]["normalized_value"] if output["office_name"] else None
    license_number = output["license_number"]["normalized_value"] if output["license_number"] else None
    return [
        ChannelClaim(
            office_name=office,
            license_number=license_number,
            channel_type=c["type"],
            value=c["normalized_value"],
            platform=c["platform"],
            attribution=c["attribution"],
            usable=c["normalized_value"] is not None,
            evidence=c["evidence"]["snippet"],
        )
        for c in output["contacts"]
    ]


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "extraction_outputs" / "ad12_waha_fake_url.json"
    output = json.loads(path.read_text(encoding="utf-8"))
    if output["status"] == "unreadable":
        print("unreadable:", output["error"])
        return
    for claim in to_claims(output):
        print(json.dumps(asdict(claim), ensure_ascii=False))


if __name__ == "__main__":
    main()
