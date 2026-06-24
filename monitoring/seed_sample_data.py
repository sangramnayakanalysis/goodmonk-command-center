"""
seed_sample_data.py — generate realistic demo history so the dashboard renders
before the first live run, and to validate json_generator end-to-end offline.

Run once locally:  python seed_sample_data.py
It writes local-fallback sheet data + a perf snapshot, then builds the JSON.
"""
import json
import random
import datetime as dt
from pathlib import Path

import config

random.seed(7)
FB = config.DATA_DIR / "_sheet_fallback"
FB.mkdir(parents=True, exist_ok=True)

now = dt.datetime.now(dt.timezone.utc).astimezone()
PAGES = config.PAGES

# ---- health history (last 48 hours, every 15 min, realistic) --------------
health = []
journey = ["Homepage", "Collection / Shop", "H50+ PDP", "Family Nutrition Mix",
           "Add to Cart", "Checkout Init"]
for i in range(192):                      # 48h * 4/hr
    ts = now - dt.timedelta(minutes=15 * (192 - i))
    for name in journey:
        base = {"Homepage": 1.6, "Collection / Shop": 2.4, "H50+ PDP": 2.9,
                "Family Nutrition Mix": 3.1, "Add to Cart": 1.2, "Checkout Init": 2.0}[name]
        lt = round(max(0.4, random.gauss(base, 0.6)), 2)
        # inject a short checkout incident ~10h ago
        incident = (name == "Checkout Init" and 38 <= (192 - i) <= 42)
        if incident:
            status, sev, code = "FAILED", "Critical", 503
        elif lt > 10:
            status, sev, code = "FAILED", "Critical", 200
        elif lt >= 4:
            status, sev, code = "WARNING", "Warning", 200
        else:
            status, sev, code = "PASS", "Healthy", 200
        health.append({"Timestamp": ts.isoformat(timespec="seconds"), "Page": name,
                       "Status": status, "HTTP_Code": code, "Load_Time": lt, "Severity": sev})

# ensure the LATEST round is all-healthy (current state = operational)
for name in journey:
    base = {"Homepage": 1.5, "Collection / Shop": 2.2, "H50+ PDP": 2.7,
            "Family Nutrition Mix": 2.9, "Add to Cart": 1.1, "Checkout Init": 1.9}[name]
    health.append({"Timestamp": now.isoformat(timespec="seconds"), "Page": name,
                   "Status": "PASS", "HTTP_Code": 200, "Load_Time": round(base, 2),
                   "Severity": "Healthy"})

(FB / "health.jsonl").write_text(
    "\n".join(json.dumps(r) for r in health), encoding="utf-8")

# ---- performance history (hourly, last 48h) -------------------------------
perf = []
profiles = {"Homepage": 58, "Collection / Shop": 64, "H50+ PDP": 47,
            "Family Nutrition Mix": 52, "Protein PDP": 55, "Checkout": 71}
for i in range(48):
    ts = now - dt.timedelta(hours=(48 - i))
    for name, m in profiles.items():
        ms = max(20, min(99, round(random.gauss(m, 5))))
        perf.append({"Timestamp": ts.isoformat(timespec="seconds"), "Page": name,
                     "Mobile_Score": ms, "Desktop_Score": min(99, ms + 25),
                     "LCP": round(random.uniform(2.4, 4.8), 2), "CLS": round(random.uniform(0.02, 0.18), 3),
                     "INP": random.randint(120, 320), "TTFB": round(random.uniform(0.4, 1.2), 2)})
(FB / "performance.jsonl").write_text(
    "\n".join(json.dumps(r) for r in perf), encoding="utf-8")

# ---- alerts history -------------------------------------------------------
alerts = [
    {"Timestamp": (now - dt.timedelta(hours=10, minutes=30)).isoformat(timespec="seconds"),
     "Severity": "Critical", "Issue": "Checkout: Checkout initiation failed (HTTP 503)", "Status": "Open"},
    {"Timestamp": (now - dt.timedelta(hours=9, minutes=45)).isoformat(timespec="seconds"),
     "Severity": "Resolved", "Issue": "Checkout: Checkout initiation failed (HTTP 503)", "Status": "Resolved"},
    {"Timestamp": (now - dt.timedelta(hours=3)).isoformat(timespec="seconds"),
     "Severity": "Critical", "Issue": "H50+ PDP: Mobile PageSpeed 47/100 — Largest images not compressed", "Status": "Open"},
]
(FB / "alerts.jsonl").write_text(
    "\n".join(json.dumps(r) for r in alerts), encoding="utf-8")

# ---- perf snapshot (latest, with diagnostics) -----------------------------
snap_pages = []
issue_bank = {
    "Homepage": ["Largest images not compressed — hero-banner.jpg (saves ~1.4s / 820KB)",
                 "Render-blocking resources — app.js (saves ~0.6s)",
                 "Images not in next-gen format (WebP/AVIF) — 6 images"],
    "Collection / Shop": ["Unused JavaScript — vendor.js (saves 410KB)",
                          "Images larger than displayed size — product thumbnails",
                          "Large total page weight (saves ~0.5s)"],
    "H50+ PDP": ["Largest images not compressed — h50-hero.png (saves ~1.8s / 1.9MB)",
                 "Render-blocking resources — reviews-widget.js",
                 "Unused CSS — theme.css (saves 230KB)"],
    "Family Nutrition Mix": ["Images not in next-gen format (WebP/AVIF) — 8 images",
                            "Slow server response (TTFB) (saves ~0.4s)",
                            "Unused JavaScript — upsell.js (saves 280KB)"],
    "Protein PDP": ["Largest images not compressed — protein-pack.jpg (saves ~1.1s)",
                    "Render-blocking resources — fonts.css",
                    "Unused JavaScript (saves 190KB)"],
    "Checkout": ["Unused JavaScript — checkout-ext.js (saves 150KB)",
                 "Render-blocking resources — payment-sdk.js",
                 "Large total page weight"],
}
keymap = {p["name"]: p["key"] for p in PAGES}
for name, ms in profiles.items():
    total_kb = random.randint(1800, 3400)
    img_kb = round(total_kb * random.uniform(0.5, 0.72))
    snap_pages.append({
        "page": keymap.get(name, name.lower()), "name": name,
        "mobile_score": max(20, min(99, round(random.gauss(ms, 3)))),
        "desktop_score": min(99, ms + 25),
        "lcp_ms": round(random.uniform(2400, 4600)), "cls": round(random.uniform(0.02, 0.16), 3),
        "inp_ms": random.randint(130, 300), "ttfb_ms": round(random.uniform(420, 980)),
        "total_kb": total_kb, "image_kb": img_kb, "image_ratio": round(img_kb / total_kb, 2),
        "top_issues": issue_bank.get(name, []),
        "weight_flag": total_kb > config.PAGE_WEIGHT_FLAG_KB,
        "image_flag": (img_kb / total_kb) > config.IMAGE_WEIGHT_FLAG_RATIO,
    })
(config.DATA_DIR / "perf_snapshot.json").write_text(
    json.dumps({"ran_at": now.isoformat(timespec="seconds"), "pages": snap_pages}, indent=2),
    encoding="utf-8")

print("Seed data written. Building dashboard JSON…")
import json_generator
json_generator.build()
print("Done.")
