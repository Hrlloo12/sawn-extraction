import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx

from app.extraction.phones import parse_loose
from integration.demo_guards import apply_demo_guards
from scripts.run_team_cases import OUT, evaluate_case, load_matcher, load_reference, outcome

DEFAULT_TRANSCRIPTIONS = ROOT / "samples" / "team_v2_images" / "transcriptions.json"


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def to_matcher_phone(value: str) -> str | None:
    parsed = parse_loose(value)
    return parsed[0].lstrip("+") if parsed else None


def confirm(result: dict, visible: list[str], matcher, entities, reports, case: dict) -> dict:
    mi = json.loads(json.dumps(result["matcher_input"]))
    shown, typed_values = [], []
    for item in mi.get("unconfirmed", []):
        if item["type"] in ("mobile", "landline", "toll_free", "whatsapp") and visible:
            typed = max(visible, key=lambda v: SequenceMatcher(None, digits(item["raw"]), digits(v)).ratio())
            shown.append({"raw": item["raw"], "typed": typed, "corrected": digits(item["raw"]) != digits(typed)})
            typed_values.append(typed)
        else:
            shown.append({"raw": item["raw"], "typed": None, "corrected": None})
    for typed in typed_values:
        phone = to_matcher_phone(typed)
        if phone and phone not in mi["phones"]:
            mi["phones"].append(phone)
    mi["unconfirmed"] = [s for s in mi.get("unconfirmed", []) if not any(x["raw"] == s["raw"] and x["typed"] for x in shown)]
    entity, channels, verdict = matcher.check(mi, entities, reports)
    final = apply_demo_guards(mi, entity, channels, verdict, entities, reports)
    read = set(result["matcher_input"]["phones"])
    missed = [v for v in visible if to_matcher_phone(v) not in read and to_matcher_phone(v) not in {to_matcher_phone(t) for t in typed_values}]
    return {
        "shown_for_confirmation": shown,
        "missed_not_shown": missed,
        "channels": channels,
        "final_verdict": final["verdict"],
        "rule": final["rule"],
        "correct": outcome(final["verdict"], case["expected_verdict"]) == "correct",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", nargs="+")
    parser.add_argument("--mode", choices=["text", "image"], default="text")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--images", type=Path, default=ROOT / "samples" / "team_v2_images")
    parser.add_argument("--transcriptions", type=Path, default=DEFAULT_TRANSCRIPTIONS)
    parser.add_argument("--source-text", choices=["transcription", "team"], default="transcription")
    parser.add_argument("--label", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    trans = json.loads(args.transcriptions.read_text(encoding="utf-8"))
    matcher = load_matcher()
    entities, reports, cases = load_reference()
    by_id = {c["id"]: c for c in cases}
    url = args.base_url.rstrip("/") + "/extract"
    runs = []
    with httpx.Client(timeout=180) as client:
        for cid in args.cases:
            case, t = by_id[cid], trans.get(cid, {})
            for attempt in range(1, args.repeat + 1):
                started = time.perf_counter()
                if args.mode == "image":
                    name = t["image"]
                    with open(args.images / name, "rb") as handle:
                        response = client.post(url, data={"input_type": "image"}, files={"file": (name, handle)}).json()
                else:
                    text = t["text"] if args.source_text == "transcription" else case["ad_text"]
                    response = client.post(url, data={"input_type": "text", "value": text}).json()
                ms = round((time.perf_counter() - started) * 1000)
                r = evaluate_case(case, response, matcher, entities, reports)
                after = confirm(r, t.get("numbers", []), matcher, entities, reports, case)
                runs.append(
                    {
                        "case": cid,
                        "attempt": attempt,
                        "ms": ms,
                        "engine": r["engine"],
                        "fallback": r["fallback_reason"],
                        "domain_status": r["domain_status"],
                        "read": {k: r["matcher_input"][k] for k in ("office_name", "phones", "urls", "ibans")},
                        "wrong_fields": {f: {"got": v["got"], "expected": v["expected"]} for f, v in r["fields"].items() if not v["ok"]},
                        "channels_before": r["channels"]["got"],
                        "verdict_before_confirmation": r["final"]["verdict"],
                        "rule_before_confirmation": r["final"]["rule"],
                        "action": r["final"]["action"],
                        "confirmation": after,
                        "verdict_after_confirmation": after["final_verdict"],
                        "expected_verdict": case["expected_verdict"],
                        "correct_before": r["final"]["outcome"] == "correct",
                        "correct_after": after["correct"],
                    }
                )
                x = runs[-1]
                print(
                    cid, attempt, f"{ms}ms", "read", x["read"]["phones"], "| before", x["verdict_before_confirmation"],
                    "| shown", [s["raw"] for s in after["shown_for_confirmation"]], "missed", after["missed_not_shown"],
                    "| after", x["verdict_after_confirmation"], "OK" if x["correct_after"] else "WRONG",
                )
    summary = {}
    for cid in args.cases:
        mine = [x for x in runs if x["case"] == cid]
        times = [x["ms"] for x in mine]
        summary[cid] = {
            "attempts": len(mine),
            "correct_before_confirmation": sum(x["correct_before"] for x in mine),
            "correct_after_confirmation": sum(x["correct_after"] for x in mine),
            "needed_confirmation": sum(bool(x["confirmation"]["shown_for_confirmation"]) for x in mine),
            "needed_correction": sum(any(s["corrected"] for s in x["confirmation"]["shown_for_confirmation"]) for x in mine),
            "missed_numbers": sum(bool(x["confirmation"]["missed_not_shown"]) for x in mine),
            "verdicts_after": sorted({x["verdict_after_confirmation"] for x in mine}),
            "all_fields_ok": sum(not x["wrong_fields"] for x in mine),
            "median_ms": round(statistics.median(times)),
            "max_ms": max(times),
        }
    meta = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": args.mode, "source_text": args.source_text}
    (OUT / f"{args.label}.json").write_text(json.dumps(meta | {"summary": summary, "runs": runs}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
