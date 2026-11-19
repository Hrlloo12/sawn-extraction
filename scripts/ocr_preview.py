import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.extraction.vision import read_domains
from app.readers.base import ReadError
from app.readers.image_reader import read_image


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image", nargs="?", default="samples/team_images/T07.png")
    args = parser.parse_args()
    settings = get_settings()
    data = Path(args.image).read_bytes()
    print(f"OCR_PROVIDER={settings.ocr_provider}  IMAGE_DOMAIN_READER={settings.image_domain_reader}")
    try:
        read, domains = await asyncio.gather(
            read_image(data, settings),
            read_domains(data, settings) if settings.image_domain_reader == "vision" else asyncio.sleep(0, None),
        )
    except ReadError as error:
        print(f"{error.code}: {error.message}")
        sys.exit(1)
    print(f"{len(read.lines)} lines from {args.image}")
    for line in read.lines:
        conf = f"{line.confidence:.2f}" if line.confidence is not None else "-"
        print(f"{line.line_no:>3}  [{conf}]  {line.text}")
    if domains is not None:
        print(f"GPT Vision domains: status={domains.status} values={domains.values} illegible={domains.illegible} {domains.error or ''}")


if __name__ == "__main__":
    asyncio.run(main())
