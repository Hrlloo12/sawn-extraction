import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from scripts.sample_spec import FORMATS, ROOT, SAMPLES, contact_key, expected_for, load_ads, page_url

ROBUSTNESS = [
    ("رابط مختصر مع تحويل", "/go/ad01_waha", "ok"),
    ("صفحة تطلب تسجيل دخول", "/private/ad01_waha", "login_required"),
    ("صفحة ترجع 401", "/members/ad01_waha", "login_required"),
    ("خطأ في الخادم 500", "/broken", "fetch_failed"),
    ("صفحة غير موجودة", "/ads/missing.html", "fetch_failed"),
    ("صفحة تعتمد على JavaScript", "/spa", "empty_page"),
    ("تحويل إلى عنوان داخلي", "/escape-internal", "domain_not_allowed"),
    ("تحويل إلى نطاق غير مسموح", "/escape-external", "domain_not_allowed"),
    ("صفحة أكبر من الحد", "/big", "response_too_large"),
    ("صفحة بطيئة", "/slow", "timeout"),
    ("منفذ مغلق", "http://127.0.0.1:8199/ad", "fetch_failed"),
    ("نطاق خارجي", "https://example.com/ad", "domain_not_allowed"),
]


class Counter:
    def __init__(self):
        self.tp = self.fp = self.fn = 0

    def add_single(self, predicted, expected):
        if predicted is not None and predicted == expected:
            self.tp += 1
            return
        if predicted is not None:
            self.fp += 1
        if expected is not None:
            self.fn += 1

    def add_sets(self, predicted: set, expected: set):
        self.tp += len(predicted & expected)
        self.fp += len(predicted - expected)
        self.fn += len(expected - predicted)

    def as_dict(self):
        precision = self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0
        recall = self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0
        return {"tp": self.tp, "fp": self.fp, "fn": self.fn, "precision": round(precision, 3), "recall": round(recall, 3)}


def call(client: httpx.Client, base: str, fmt: str, payload):
    started = time.perf_counter()
    if fmt == "image":
        response = client.post(f"{base}/extract", data={"input_type": "image"}, files={"file": ("ad.png", payload, "image/png")})
    else:
        response = client.post(f"{base}/extract", data={"input_type": fmt, "value": payload})
    elapsed = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return response.json(), elapsed


def payload_for(ad, fmt, demo_base):
    if fmt == "text":
        return (SAMPLES / "text" / f"{ad['id']}.txt").read_text(encoding="utf-8")
    if fmt == "url":
        return page_url(ad, demo_base)
    return (SAMPLES / "images" / f"{ad['id']}.png").read_bytes()


def score(body, expected, counters, mistakes, label):
    office = body["office_name"]["normalized_value"] if body["office_name"] else None
    license_number = body["license_number"]["normalized_value"] if body["license_number"] else None
    counters["office_name"].add_single(office, expected["office_name"])
    counters["license_number"].add_single(license_number, expected["license_number"])
    predicted = {contact_key(c) for c in body["contacts"]}
    wanted = {contact_key(c) for c in expected["contacts"]}
    counters["contacts"].add_sets(predicted, wanted)
    if "platform_contacts" in expected:
        counters["platform_contacts"].add_sets(
            {contact_key(c) for c in body["platform_contacts"]}, {contact_key(c) for c in expected["platform_contacts"]}
        )
    by_key = {contact_key(c): c for c in body["contacts"]}
    attribution = [0, 0]
    for item in expected["contacts"]:
        got = by_key.get(contact_key(item))
        if got:
            attribution[1] += 1
            attribution[0] += got["attribution"] == item.get("attribution", "advertiser")
    status_ok = "status" not in expected or body["status"] == expected["status"]
    problems = []
    if office != expected["office_name"]:
        problems.append(f"office_name: got {office!r}, expected {expected['office_name']!r}")
    if license_number != expected["license_number"]:
        problems.append(f"license_number: got {license_number!r}, expected {expected['license_number']!r}")
    if predicted - wanted:
        problems.append(f"extra contacts: {sorted(predicted - wanted, key=str)}")
    if wanted - predicted:
        problems.append(f"missing contacts: {sorted(wanted - predicted, key=str)}")
    if not status_ok:
        problems.append(f"status: got {body['status']}, expected {expected['status']}")
    if attribution[0] != attribution[1]:
        problems.append("attribution mismatch")
    if problems:
        mistakes.append({"case": label, "problems": problems})
    return status_ok, attribution


def percentile(values, q):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def evaluate(base: str, demo_base: str, repeat: int):
    ads = load_ads()
    holdout = json.loads((SAMPLES / "holdout.json").read_text(encoding="utf-8"))
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "base_url": base, "repeat": repeat}
    with httpx.Client(timeout=180) as client:
        report["health"] = client.get(f"{base}/health").json()
        per_format = {}
        mistakes = []
        for fmt in FORMATS:
            counters = defaultdict(Counter)
            status_hits = 0
            attribution = [0, 0]
            client_ms, server_ms = [], []
            for ad in ads:
                payload = payload_for(ad, fmt, demo_base)
                expected = expected_for(ad, fmt)
                for run in range(repeat):
                    body, elapsed = call(client, base, fmt, payload)
                    client_ms.append(elapsed)
                    server_ms.append(body["timing_ms"]["total"])
                    if run == 0:
                        ok, attr = score(body, expected, counters, mistakes, f"{ad['id']} [{fmt}]")
                        status_hits += ok
                        attribution[0] += attr[0]
                        attribution[1] += attr[1]
            per_format[fmt] = {
                "samples": len(ads),
                "requests": len(client_ms),
                "fields": {k: v.as_dict() for k, v in counters.items()},
                "status_accuracy": round(status_hits / len(ads), 3),
                "attribution_accuracy": round(attribution[0] / attribution[1], 3) if attribution[1] else None,
                "latency_ms": {
                    "client_p50": round(percentile(client_ms, 0.5), 1),
                    "client_p95": round(percentile(client_ms, 0.95), 1),
                    "client_mean": round(statistics.mean(client_ms), 1),
                    "server_p50": percentile(server_ms, 0.5),
                    "server_p95": percentile(server_ms, 0.95),
                },
            }
        report["formats"] = per_format

        counters = defaultdict(Counter)
        holdout_mistakes = []
        attribution = [0, 0]
        for item in holdout:
            body, _ = call(client, base, "text", item["text"])
            _, attr = score(body, item["expected"], counters, holdout_mistakes, item["id"])
            attribution[0] += attr[0]
            attribution[1] += attr[1]
        report["holdout_text"] = {
            "samples": len(holdout),
            "fields": {k: v.as_dict() for k, v in counters.items()},
            "attribution_accuracy": round(attribution[0] / attribution[1], 3) if attribution[1] else None,
            "mistakes": holdout_mistakes,
        }
        report["mistakes"] = mistakes

        robustness = []
        for label, path, expected_code in ROBUSTNESS:
            url = path if path.startswith("http") else demo_base + path
            body, elapsed = call(client, base, "url", url)
            code = body["error"]["code"] if body["error"] else body["status"]
            robustness.append(
                {"case": label, "url": url, "expected": expected_code, "got": code, "pass": code == expected_code, "ms": round(elapsed)}
            )
        report["robustness"] = robustness
    return report


def to_markdown(report) -> str:
    out = [
        "# تقرير تقييم خدمة الاستخراج",
        "",
        f"- وقت التشغيل: {report['generated_at']}",
        f"- الخدمة: {report['base_url']} (OCR: `{report['health']['ocr_provider']}`, Azure مفعّل: {report['health']['azure_configured']})",
        f"- كل عينة أُرسلت {report['repeat']} مرات لقياس الزمن؛ الدقة محسوبة من أول تشغيل.",
        "",
        "## الدقة والاسترجاع على مستوى الحقول (بعد توحيد الصيغ)",
        "",
        "| المسار | العينات | الحقل | TP | FP | FN | الدقة | الاسترجاع |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for fmt, data in report["formats"].items():
        for field, m in data["fields"].items():
            out.append(f"| {fmt} | {data['samples']} | {field} | {m['tp']} | {m['fp']} | {m['fn']} | {m['precision']:.3f} | {m['recall']:.3f} |")
    out += ["", "| المسار | دقة status | دقة نسبة القناة (attribution) |", "|---|---|---|"]
    for fmt, data in report["formats"].items():
        out.append(f"| {fmt} | {data['status_accuracy']:.3f} | {data['attribution_accuracy']} |")
    out += [
        "",
        "## زمن الاستجابة (ms)",
        "",
        "| المسار | الطلبات | p50 عميل | p95 عميل | متوسط عميل | p50 خادم | p95 خادم |",
        "|---|---|---|---|---|---|---|",
    ]
    for fmt, data in report["formats"].items():
        lat = data["latency_ms"]
        out.append(
            f"| {fmt} | {data['requests']} | {lat['client_p50']} | {lat['client_p95']} | {lat['client_mean']} | {lat['server_p50']} | {lat['server_p95']} |"
        )
    hold = report["holdout_text"]
    out += [
        "",
        f"## مجموعة اختبار منفصلة (نص فقط، {hold['samples']} إعلانات لم تُضبط عليها القواعد)",
        "",
        "| الحقل | TP | FP | FN | الدقة | الاسترجاع |",
        "|---|---|---|---|---|---|",
    ]
    for field, m in hold["fields"].items():
        out.append(f"| {field} | {m['tp']} | {m['fp']} | {m['fn']} | {m['precision']:.3f} | {m['recall']:.3f} |")
    out.append(f"\nدقة نسبة القناة: {hold['attribution_accuracy']}\n")
    if hold["mistakes"]:
        out.append("أخطاء مجموعة الاختبار المنفصلة:\n")
        for m in hold["mistakes"]:
            out.append(f"- `{m['case']}`: " + "؛ ".join(m["problems"]))
    out += ["", "## أخطاء العينات الأساسية", ""]
    if report["mistakes"]:
        for m in report["mistakes"]:
            out.append(f"- `{m['case']}`: " + "؛ ".join(m["problems"]))
    else:
        out.append("لا توجد.")
    out += ["", "## حالات تعذّر القراءة والأمان (مسار الرابط)", "", "| الحالة | المتوقع | الناتج | النتيجة | ms |", "|---|---|---|---|---|"]
    for r in report["robustness"]:
        out.append(f"| {r['case']} | {r['expected']} | {r['got']} | {'✅' if r['pass'] else '❌'} | {r['ms']} |")
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--demo-base", default="http://127.0.0.1:8100")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--name", default="evaluation")
    args = parser.parse_args()
    report = evaluate(args.base_url.rstrip("/"), args.demo_base.rstrip("/"), args.repeat)
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / f"{args.name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (reports / f"{args.name}.md").write_text(to_markdown(report), encoding="utf-8")
    print(to_markdown(report))


if __name__ == "__main__":
    main()
