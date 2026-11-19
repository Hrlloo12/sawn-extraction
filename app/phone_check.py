import importlib.util
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.extraction.normalize import normalize_text
from app.extraction.phones import parse_loose
from app.schemas import PhoneCheckOffice, PhoneCheckReport, PhoneCheckResponse


class InvalidPhone(ValueError):
    pass


@dataclass
class Reference:
    matcher: object
    entities: list[dict]
    reports: list[dict]


@lru_cache
def load_reference(directory: str) -> Reference:
    root = Path(directory)
    root = root if root.is_absolute() else PROJECT_ROOT / root
    spec = importlib.util.spec_from_file_location("sawn_team_matcher", root / "matcher.py")
    matcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(matcher)
    entities = json.loads((root / "entities.json").read_text(encoding="utf-8"))["entities"]
    reports = json.loads((root / "reports.json").read_text(encoding="utf-8"))["reports"]
    return Reference(matcher, entities, reports)


def _office(entity: dict | None) -> PhoneCheckOffice | None:
    if not entity:
        return None
    return PhoneCheckOffice(
        id=entity["id"],
        name_ar=entity["name_ar"],
        name_en=entity["name_en"],
        license_no=entity["license_no"],
        city=entity["city"],
    )


def check_phone(raw: str, settings: Settings) -> PhoneCheckResponse:
    parsed = parse_loose(normalize_text(raw or "").text)
    if not parsed:
        raise InvalidPhone(raw)
    e164, phone_type = parsed
    value = e164.lstrip("+")
    ref = load_reference(settings.reference_data_dir)
    entity_id, channels, verdict = ref.matcher.check(
        {"office_name": "", "phones": [value], "urls": [], "ibans": []}, ref.entities, ref.reports
    )
    by_id = {e["id"]: e for e in ref.entities}
    outcome = channels.get(value, "unregistered")
    report = next((r for r in ref.reports if r["value"] == value), None)
    office = _office(by_id.get(entity_id))
    if outcome == "match":
        result, message = "registered", f"الرقم مسجّل في القاعدة التجريبية لـ{office.name_ar}"
    elif report:
        claimed = by_id.get(report["claimed_entity"])
        result = "reported"
        message = f"الرقم غير مسجّل لأي مكتب، ومُبلَّغ عنه {report['count']} مرات أنه ينتحل {claimed['name_ar'] if claimed else report['claimed_entity']}"
    else:
        result, message = "unregistered", "الرقم غير مسجّل في القاعدة التجريبية"
    return PhoneCheckResponse(
        input=raw,
        normalized_value=e164,
        phone_type=phone_type,
        result=result,
        registered=result == "registered",
        matcher_result=outcome,
        verdict=verdict,
        message=message,
        office=office,
        report=PhoneCheckReport(
            id=report["id"],
            claimed_office=_office(by_id.get(report["claimed_entity"])),
            count=report["count"],
            last_seen=report["last_seen"],
        )
        if report
        else None,
    )
