UNKNOWN = "لا نعرف"
NOT_BELONGS = "لا يعود"
BELONGS = "يعود"


def _known_elsewhere(value: str, entity_id: str | None, entities: list[dict], reports: list[dict]) -> bool:
    if value in {r["value"] for r in reports}:
        return True
    for e in entities:
        if value in e.get("domains", []) and e["id"] != entity_id:
            return True
    return False


def _confirm_text(unconfirmed: list[dict]) -> str:
    return "، ".join(item["raw"] for item in unconfirmed)


def apply_demo_guards(
    matcher_input: dict,
    entity_id: str | None,
    channels: dict,
    verdict: str,
    entities: list[dict],
    reports: list[dict],
) -> dict:
    result = {"verdict": verdict, "matcher_verdict": verdict, "rule": None, "action": None}
    unconfirmed = matcher_input.get("unconfirmed", [])
    if not (matcher_input.get("phones") or matcher_input.get("urls") or matcher_input.get("ibans")):
        action = "لم تصل أي قناة صالحة للمطابقة. "
        action += f"أكّد القراءة: {_confirm_text(unconfirmed)}" if unconfirmed else "اطلب من المستخدم لصق نص الإعلان أو رفع صورة أوضح."
        result.update(verdict=UNKNOWN, rule="no_valid_channel", action=action)
        return result
    if verdict == BELONGS and unconfirmed:
        result.update(
            verdict=UNKNOWN,
            rule="unconfirmed_channel",
            action=f"في الإعلان قناة لم تُقرأ بثقة: {_confirm_text(unconfirmed)}. اطلب من المستخدم تأكيدها قبل الحكم.",
        )
        return result
    if verdict != NOT_BELONGS:
        return result
    ocr_urls = set(matcher_input.get("ocr_urls", []))
    failing = [value for value, outcome in channels.items() if outcome == "no_match"]
    only_ocr_domains = failing and all(
        value in ocr_urls and not _known_elsewhere(value, entity_id, entities, reports) for value in failing
    )
    if only_ocr_domains:
        result.update(
            verdict=UNKNOWN,
            rule="ocr_domain_mismatch",
            action="الدومين مقروء من صورة ويختلف عن الرسمي: "
            + "، ".join(failing)
            + ". اطلب من المستخدم لصق الرابط أو تأكيد قراءته قبل الحكم.",
        )
    return result
