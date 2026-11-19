import argparse
import importlib.util
import json
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integration.demo_guards import UNKNOWN, apply_demo_guards
from integration.matcher_adapter import MATCHER_FIELDS, domain_only, to_matcher_input

REF = ROOT / "team_data"
OUT = ROOT / "reports" / "team_cases"
TEAM_IMAGES = ROOT / "samples" / "team_images"
MATCHER_INPUT_FIELDS = ("office_name", "phones", "urls", "ibans")
RUN_ORDER = [
    ("before", "نص: القواعد قبل حقول الدفع"),
    ("rules_text", "نص: القواعد"),
    ("gpt_text", "نص: gpt-5-mini (تجربة)"),
    ("final_text", "**نص: gpt-5-mini + رجوع للقواعد (المعتمد)**"),
    ("rules_image", "صورة: Tesseract + القواعد"),
    ("gpt_image", "صورة: Tesseract + gpt-5-mini (تجربة)"),
    ("final_image", "**صورة: Tesseract + القواعد + دومين GPT Vision (المعتمد)**"),
]
NON_DOMAIN_KEYS = ("office_name", "license_number", "ibans", "account_holder", "payment", "platform_contacts")


def load_matcher():
    spec = importlib.util.spec_from_file_location("team_matcher", REF / "matcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_reference():
    def read(name):
        return json.loads((REF / name).read_text(encoding="utf-8"))

    return read("entities.json")["entities"], read("reports.json")["reports"], read("test_cases.json")["cases"]


def plain(text: str) -> str:
    text = "".join(ch for ch in unicodedata.normalize("NFKC", text or "") if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip(" .،,!؟?:-")


def same(field: str, got, expected) -> bool:
    if field in ("phones", "urls", "ibans"):
        return sorted(set(got)) == sorted(set(expected))
    if field == "urgency_phrases":
        return sorted({plain(x) for x in got}) == sorted({plain(x) for x in expected})
    if field in ("office_name", "account_holder"):
        return plain(got) == plain(expected)
    return got == expected


def field_cause(field: str, response: dict, expected) -> str:
    present = set()
    for group in ("contacts", "platform_contacts"):
        for c in response.get(group, []):
            if c.get("normalized_value"):
                present.add(c["normalized_value"].lstrip("+"))
                if c["type"] == "website":
                    present.add(domain_only(c["normalized_value"]))
    present |= {i["normalized_value"] for i in response.get("ibans", []) if i.get("normalized_value")}
    if field in ("phones", "urls", "ibans") and expected and set(expected) <= present:
        return "adapter"
    return "extraction"


def outcome(verdict: str, expected: str) -> str:
    if verdict == expected:
        return "correct"
    return "unknown" if verdict == UNKNOWN else "wrong"


def evaluate_case(case: dict, response: dict, matcher, entities, reports) -> dict:
    expected = case["expected_extraction"]
    got = to_matcher_input(response)
    fields = {}
    for field in MATCHER_FIELDS:
        ok = same(field, got[field], expected[field])
        fields[field] = {"ok": ok, "got": got[field], "expected": expected[field]}
        if not ok:
            fields[field]["cause"] = field_cause(field, response, expected[field])
    entity, channels, verdict = matcher.check(got, entities, reports)
    guarded = apply_demo_guards(got, entity, channels, verdict, entities, reports)
    entity_ok = entity == case["expected_entity"]
    channels_ok = channels == case["expected_channels"]
    verdict_ok = verdict == case["expected_verdict"]
    if verdict_ok and entity_ok and channels_ok:
        cause = None
    elif all(fields[f]["ok"] for f in MATCHER_INPUT_FIELDS):
        cause = "matching_rule"
    else:
        cause = "+".join(sorted({fields[f]["cause"] for f in MATCHER_INPUT_FIELDS if not fields[f]["ok"]}))
    info = response.get("extractor") or {}
    return {
        "id": case["id"],
        "category": case["category"],
        "status": response.get("status"),
        "engine": info.get("name", "rules"),
        "fallback_reason": info.get("fallback_reason"),
        "domain_status": info.get("domain_status"),
        "extraction_ok": all(v["ok"] for v in fields.values()),
        "fields": fields,
        "matcher_input": got,
        "entity": {"got": entity, "expected": case["expected_entity"], "ok": entity_ok},
        "channels": {"got": channels, "expected": case["expected_channels"], "ok": channels_ok},
        "verdict": {"got": verdict, "expected": case["expected_verdict"], "ok": verdict_ok},
        "decision_ok": verdict_ok and entity_ok and channels_ok,
        "verdict_cause": cause,
        "final": guarded | {"outcome": outcome(guarded["verdict"], case["expected_verdict"])},
    }


def attribute_to_image(results: list[dict], text_label: str):
    path = OUT / f"{text_label}.json"
    if not path.exists():
        return
    base = {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["results"]}
    for r in results:
        for field, value in r["fields"].items():
            if not value["ok"] and r["id"] in base and base[r["id"]]["fields"][field]["ok"]:
                value["cause"] = "image_reading"
        if r["verdict_cause"] and r["verdict_cause"] != "matching_rule":
            r["verdict_cause"] = "+".join(
                sorted({r["fields"][f]["cause"] for f in MATCHER_INPUT_FIELDS if not r["fields"][f]["ok"]})
            )


def exact_values(results: list[dict], field: str) -> list[int]:
    found = total = 0
    for r in results:
        expected = set(r["fields"][field]["expected"])
        total += len(expected)
        found += len(expected & set(r["fields"][field]["got"]))
    return [found, total]


def percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]


def summarize(results: list[dict], latencies: dict | None = None) -> dict:
    per_field = {f: sum(r["fields"][f]["ok"] for r in results) for f in MATCHER_FIELDS}
    summary = {
        "cases": len(results),
        "extraction_cases_ok": sum(r["extraction_ok"] for r in results),
        "fields_ok": sum(per_field.values()),
        "fields_total": len(results) * len(MATCHER_FIELDS),
        "per_field": per_field,
        "phones_exact": exact_values(results, "phones"),
        "ibans_exact": exact_values(results, "ibans"),
        "urls_exact": exact_values(results, "urls"),
        "verdicts_ok": sum(r["verdict"]["ok"] for r in results),
        "decisions_ok": sum(r["decision_ok"] for r in results),
        "outcomes": dict(Counter(r["final"]["outcome"] for r in results)),
        "guard_rules": {r["id"]: r["final"]["rule"] for r in results if r["final"]["rule"]},
        "lucky_verdicts": [r["id"] for r in results if r["verdict"]["ok"] and not r["decision_ok"]],
        "engines": dict(Counter(r["engine"] for r in results)),
        "fallbacks": {r["id"]: r["fallback_reason"] for r in results if r["fallback_reason"]},
        "domain_status": dict(Counter(r["domain_status"] for r in results if r["domain_status"])),
    }
    if latencies:
        values = list(latencies.values())
        summary["latency_ms"] = {
            "mean": round(statistics.mean(values)),
            "median": round(statistics.median(values)),
            "p95": percentile(values, 0.95),
            "max": max(values),
            "per_case": latencies,
        }
    return summary


def short(value) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(map(str, value)) + "]"
    return value if isinstance(value, str) and value else json.dumps(value, ensure_ascii=False)


def write_markdown(label: str, meta: dict, summary: dict, results: list[dict]):
    n = summary["cases"]
    o = summary["outcomes"]
    lat = summary.get("latency_ms")
    exact = " · ".join("{}/{}".format(*summary[k]) for k in ("phones_exact", "ibans_exact", "urls_exact"))
    lines = [
        f"# حالات الفريق العشرون: {label}",
        "",
        f"- وقت التشغيل: {meta.get('time')}، المدخل: `{meta.get('input')}`، الخدمة: {meta.get('source')}",
        f"- المحرك المطلوب: `{meta.get('extractor')}`، المحرك الذي اشتغل: {summary['engines']}"
        + (f"، حالة الدومين: {summary['domain_status']}" if summary["domain_status"] else ""),
        "- المطابِق `team_data/matcher.py` كما هو، بعد `integration/matcher_adapter.py`، ثم قاعدتا `integration/demo_guards.py`.",
        "",
        "| المقياس | النتيجة |",
        "|---|---|",
        f"| إعلانات كل حقولها صح | {summary['extraction_cases_ok']}/{n} |",
        f"| حقول مطابقة | {summary['fields_ok']}/{summary['fields_total']} |",
        f"| أرقام · آيبانات · دومينات بالضبط | {exact} |",
        f"| حكم المطابِق الخام صحيح | {summary['verdicts_ok']}/{n} (قرار كامل: {summary['decisions_ok']}/{n}) |",
        f"| الحكم النهائي بعد قاعدتي الديمو: صحيح / خطأ / لا نعرف | {o.get('correct', 0)} / {o.get('wrong', 0)} / {o.get('unknown', 0)} |",
        f"| قاعدتا الديمو غيّرتا | {summary['guard_rules'] or 'لا شيء'} |",
        f"| أحكام صحيحة بقنوات مختلفة | {', '.join(summary['lucky_verdicts']) or 'لا يوجد'} |",
        f"| رجوع للقواعد | {summary['fallbacks'] or 'لا يوجد'} |",
    ]
    if lat:
        lines.append(f"| الزمن: متوسط / وسيط / p95 / أقصى | {lat['mean']} / {lat['median']} / {lat['p95']} / {lat['max']} ms |")
    lines += ["", "| الحقل | مطابق |", "|---|---|"] + [f"| `{f}` | {c}/{n} |" for f, c in summary["per_field"].items()]
    lines += ["", "## الحالات المختلفة", ""]
    diffs = [r for r in results if not (r["extraction_ok"] and r["decision_ok"] and r["final"]["outcome"] == "correct")]
    for r in diffs:
        final = r["final"]
        lines.append(
            f"### {r['id']} ({r['category']}): المطابِق {r['verdict']['got']}، النهائي {final['verdict']} ({final['outcome']})، المتوقع {r['verdict']['expected']}"
        )
        lines.append("")
        for f, v in r["fields"].items():
            if not v["ok"]:
                lines.append(f"- `{f}` ({v['cause']}): الناتج {short(v['got'])}، المتوقع {short(v['expected'])}")
        if not r["channels"]["ok"]:
            lines.append(f"- القنوات: الناتج {short(r['channels']['got'])}، المتوقع {short(r['channels']['expected'])}")
        if r["verdict_cause"]:
            lines.append(f"- سبب اختلاف المطابقة: `{r['verdict_cause']}`")
        if final["rule"]:
            lines.append(f"- قاعدة الديمو `{final['rule']}`: {final['action']}")
        lines.append("")
    if not diffs:
        lines.append("لا توجد.")
    (OUT / f"{label}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def save(label: str, meta: dict, summary: dict, results: list[dict]):
    (OUT / f"{label}.json").write_text(
        json.dumps({"meta": meta, "summary": summary, "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(label, meta, summary, results)
    write_comparison()


def write_comparison():
    header = ["المسار", "إعلانات كل حقولها صح", "حقول", "أرقام بالضبط", "آيبانات بالضبط", "دومينات بالضبط"]
    header += ["صحيح", "خطأ", "لا نعرف", "وسيط الزمن", "p95", "ملاحظات"]
    rows = []
    for label, name in RUN_ORDER:
        path = OUT / f"{label}.json"
        if not path.exists():
            continue
        s = json.loads(path.read_text(encoding="utf-8"))["summary"]
        n = s["cases"]
        o = s.get("outcomes", {})
        lat = s.get("latency_ms") or {}
        notes = []
        if s.get("fallbacks"):
            notes.append(f"رجوع للقواعد: {', '.join(s['fallbacks'])}")
        if s.get("guard_rules"):
            notes.append("لا نعرف بقاعدة: " + ", ".join(f"{k} ({v})" for k, v in s["guard_rules"].items()))
        if s.get("lucky_verdicts"):
            notes.append("صحيح بقنوات ناقصة: " + ", ".join(s["lucky_verdicts"]))
        cells = [name, f"{s['extraction_cases_ok']}/{n}", f"{s['fields_ok']}/{s['fields_total']}"]
        cells += ["{}/{}".format(*s[k]) for k in ("phones_exact", "ibans_exact", "urls_exact")]
        cells += [str(o.get(k, 0)) for k in ("correct", "wrong", "unknown")]
        cells += [f"{lat['median']} ms" if lat else "-", f"{lat['p95']} ms" if "p95" in lat else "-", "؛ ".join(notes) or "-"]
        rows.append("| " + " | ".join(cells) + " |")
    lines = [
        "# مقارنة مسارات الاستخراج على حالات الفريق العشرين",
        "",
        "يُولَّد من `scripts/run_team_cases.py`. صحيح / خطأ / لا نعرف هي الحكم النهائي بعد `matcher.py` وقاعدتي `integration/demo_guards.py`.",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *rows,
        "",
    ]
    (OUT / "comparison.md").write_text("\n".join(lines), encoding="utf-8")


def run(post, label: str, source: str, save_results: bool = True, meta_extra: dict | None = None, only: list[str] | None = None) -> tuple[dict, list[dict]]:
    matcher = load_matcher()
    entities, reports, cases = load_reference()
    cases = [c for c in cases if not only or c["id"] in only]
    results, latencies = [], {}
    folder = OUT / label
    if save_results:
        folder.mkdir(parents=True, exist_ok=True)
    for case in cases:
        started = time.perf_counter()
        response = post(case)
        latencies[case["id"]] = round((time.perf_counter() - started) * 1000)
        results.append(evaluate_case(case, response, matcher, entities, reports))
        if save_results:
            body = {"extract_response": response, "matcher_input": results[-1]["matcher_input"]}
            (folder / f"{case['id']}.json").write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": source} | (meta_extra or {})
    if meta.get("input") == "image":
        attribute_to_image(results, meta.get("text_label", "final_text"))
    summary = summarize(results, latencies)
    if save_results:
        save(label, meta, summary, results)
    return summary, results


def rescore(label: str) -> dict:
    matcher = load_matcher()
    entities, reports, cases = load_reference()
    old = json.loads((OUT / f"{label}.json").read_text(encoding="utf-8"))
    meta = old["meta"] | {"rescored": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    latencies = (old["summary"].get("latency_ms") or {}).get("per_case")
    results = []
    for case in cases:
        response = json.loads((OUT / label / f"{case['id']}.json").read_text(encoding="utf-8"))["extract_response"]
        results.append(evaluate_case(case, response, matcher, entities, reports))
    if meta.get("input") == "image":
        attribute_to_image(results, "gpt_text" if meta.get("extractor") == "gpt" else "rules_text")
    summary = summarize(results, latencies)
    save(label, meta, summary, results)
    return summary


def compare_non_domain(label: str, other: str) -> dict:
    def contacts(r):
        return [c for c in r["contacts"] if c["type"] != "website"]

    def warnings(r):
        return [w["code"] for w in r["warnings"] if not w["code"].startswith("domain") and w["code"] not in ("invalid_domain", "ambiguous_link")]

    identical, diffs = 0, {}
    for path in sorted((OUT / label).glob("T*.json")):
        a = json.loads(path.read_text(encoding="utf-8"))["extract_response"]
        b = json.loads((OUT / other / path.name).read_text(encoding="utf-8"))["extract_response"]
        differing = [k for k in NON_DOMAIN_KEYS if a[k] != b[k]]
        differing += ["contacts"] if contacts(a) != contacts(b) else []
        differing += ["warnings"] if warnings(a) != warnings(b) else []
        if differing:
            diffs[path.stem] = differing
        else:
            identical += 1
    return {"identical": identical, "differences": diffs}


def http_poster(base_url: str, extractor: str | None, input_type: str):
    import httpx

    client = httpx.Client(timeout=180)
    url = base_url.rstrip("/") + "/extract"
    extra = {"extractor": extractor} if extractor else {}

    def post(case: dict) -> dict:
        if input_type == "image":
            path = TEAM_IMAGES / f"{case['id']}.png"
            with open(path, "rb") as handle:
                response = client.post(url, data={"input_type": "image", **extra}, files={"file": (path.name, handle, "image/png")})
        else:
            response = client.post(url, data={"input_type": "text", "value": case["ad_text"], **extra})
        response.raise_for_status()
        body = response.json()
        if (body.get("error") or {}).get("code") == "ocr_not_configured":
            raise SystemExit(f"OCR غير جاهز: {body['error']['message']}")
        return body

    return post, client


def print_summary(label: str, summary: dict):
    n = summary["cases"]
    o = summary["outcomes"]
    print(f"[{label}] extraction {summary['extraction_cases_ok']}/{n} cases, {summary['fields_ok']}/{summary['fields_total']} fields")
    print(f"  exact phones {summary['phones_exact']} ibans {summary['ibans_exact']} urls {summary['urls_exact']}")
    print(f"  matcher verdicts {summary['verdicts_ok']}/{n}, decisions {summary['decisions_ok']}/{n}")
    print(f"  final correct {o.get('correct', 0)} wrong {o.get('wrong', 0)} unknown {o.get('unknown', 0)} rules {summary['guard_rules']}")
    print(f"  engines {summary['engines']} fallbacks {summary['fallbacks']} domain {summary['domain_status']}")
    if summary.get("latency_ms"):
        lat = summary["latency_ms"]
        print(f"  latency mean {lat['mean']} median {lat['median']} p95 {lat['p95']} max {lat['max']} ms")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", choices=["text", "image"], default="text")
    parser.add_argument("--extractor", choices=["rules", "gpt"], help="بدونه: إعداد الخادم الافتراضي")
    parser.add_argument("--label")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--rescore", nargs="+", metavar="LABEL", help="إعادة تقييم ردود محفوظة بدون استدعاء الخدمة")
    parser.add_argument("--same-as", metavar="LABEL", help="مقارنة كل الحقول غير الدومين مع تشغيل محفوظ")
    parser.add_argument("--cases", nargs="+", help="تشغيل حالات محددة فقط، مثل T01 T02")
    args = parser.parse_args()
    if args.rescore:
        for label in args.rescore:
            print_summary(label, rescore(label))
        return
    label = args.label or f"{args.extractor or 'final'}_{args.input}"
    post, client = http_poster(args.base_url, args.extractor, args.input)
    health = client.get(args.base_url.rstrip("/") + "/health").json()
    meta = {"input": args.input, "extractor": args.extractor or f"server default ({health.get('extractor')})", "health": health}
    summary, _ = run(post, label, args.base_url, meta_extra=meta, only=args.cases)
    print_summary(label, summary)
    if args.same_as:
        check = compare_non_domain(label, args.same_as)
        print(f"  non-domain fields identical to {args.same_as}: {check['identical']}/{summary['cases']} {check['differences']}")
        data = json.loads((OUT / f"{label}.json").read_text(encoding="utf-8"))
        data["summary"]["non_domain_vs"] = {args.same_as: check}
        (OUT / f"{label}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report: {OUT / (label + '.md')}")


if __name__ == "__main__":
    main()
