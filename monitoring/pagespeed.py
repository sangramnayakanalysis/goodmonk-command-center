"""
pagespeed.py — Google PageSpeed Insights (Lighthouse) client.

For each target page it fetches BOTH mobile and desktop strategies and returns:
  * performance score (0-100) per strategy
  * Core Web Vitals: LCP, CLS, INP/TBT proxy, TTFB
  * total page weight + image weight
  * the top 3 diagnosed "opportunities" (why it's slow), with file names + KB

Only depends on `requests` and the free PageSpeed API key.
"""

import time
import requests

import config

# Lighthouse audits we mine for the "why is it slow" diagnostics.
_OPPORTUNITY_AUDITS = [
    ("uses-optimized-images", "Largest images not compressed"),
    ("uses-responsive-images", "Images larger than displayed size"),
    ("modern-image-formats", "Images not in next-gen format (WebP/AVIF)"),
    ("unused-javascript", "Unused JavaScript"),
    ("unused-css-rules", "Unused CSS"),
    ("render-blocking-resources", "Render-blocking resources"),
    ("server-response-time", "Slow server response (TTFB)"),
    ("efficient-animated-content", "Heavy animated content"),
    ("total-byte-weight", "Large total page weight"),
]


def _kb(bytes_val) -> float:
    try:
        return round(float(bytes_val) / 1024.0, 1)
    except (TypeError, ValueError):
        return 0.0


def _audit_savings_label(audit: dict) -> str:
    """Build a human label like 'Render-blocking resources (saves 1.2s / 340KB)'."""
    details = audit.get("details", {}) or {}
    overall = details.get("overallSavingsMs")
    bytes_saving = details.get("overallSavingsBytes")
    parts = []
    if overall:
        parts.append(f"~{round(overall/1000.0, 1)}s")
    if bytes_saving:
        parts.append(f"{_kb(bytes_saving)}KB")

    # Pull the single biggest contributing file, if present.
    items = details.get("items") or []
    biggest = ""
    if items:
        first = items[0]
        url = first.get("url") or first.get("source") or ""
        if url:
            biggest = url.split("/")[-1].split("?")[0][:60]
    label = audit.get("title", "Issue")
    extra = f" — {biggest}" if biggest else ""
    saving = f" (saves {' / '.join(parts)})" if parts else ""
    return f"{label}{extra}{saving}"


def _run_one(page_key: str, strategy: str) -> dict:
    url = config.url_for(page_key)
    params = {
        "url": url,
        "strategy": strategy,            # "mobile" | "desktop"
        "category": "performance",
    }
    if config.PAGESPEED_API_KEY:
        params["key"] = config.PAGESPEED_API_KEY

    for attempt in range(3):
        try:
            r = requests.get(config.PAGESPEED_ENDPOINT, params=params, timeout=120)
            if r.status_code == 200:
                return r.json()
            print(f"[pagespeed] {page_key}/{strategy} HTTP {r.status_code} (attempt {attempt+1})")
        except requests.RequestException as exc:
            print(f"[pagespeed] {page_key}/{strategy} error: {exc} (attempt {attempt+1})")
        time.sleep(5 * (attempt + 1))
    return {}


def _extract(lh: dict) -> dict:
    audits = lh.get("audits", {})
    cat = lh.get("categories", {}).get("performance", {})
    score = round((cat.get("score") or 0) * 100)

    def num(audit_id, default=0.0):
        return audits.get(audit_id, {}).get("numericValue", default) or default

    lcp_ms = num("largest-contentful-paint")
    cls = num("cumulative-layout-shift")
    # PSI field data carries INP; lab data uses TBT as the responsiveness proxy.
    inp_ms = num("interaction-to-next-paint") or num("total-blocking-time")
    ttfb_ms = num("server-response-time")
    total_kb = _kb(num("total-byte-weight"))

    # Image weight from the resource-summary audit.
    image_kb = 0.0
    rs = audits.get("resource-summary", {}).get("details", {}).get("items", [])
    for it in rs:
        if it.get("resourceType") == "image":
            image_kb = _kb(it.get("transferSize", 0))
            break

    # Top 3 opportunities by potential savings.
    found = []
    for audit_id, _ in _OPPORTUNITY_AUDITS:
        a = audits.get(audit_id)
        if not a:
            continue
        if (a.get("score") is None) or (a.get("score", 1) >= 0.9):
            continue  # passing audit, not an opportunity
        savings = (a.get("details", {}) or {}).get("overallSavingsMs", 0) or \
                  (a.get("details", {}) or {}).get("overallSavingsBytes", 0)
        found.append((savings, _audit_savings_label(a)))
    found.sort(key=lambda x: x[0], reverse=True)
    top3 = [label for _, label in found[:3]]

    return {
        "score": score,
        "lcp_ms": lcp_ms,
        "cls": cls,
        "inp_ms": inp_ms,
        "ttfb_ms": ttfb_ms,
        "total_kb": total_kb,
        "image_kb": image_kb,
        "top_issues": top3,
    }


def measure_page(page_key: str) -> dict:
    """Run mobile + desktop and merge into one record for a page."""
    mobile_raw = _run_one(page_key, "mobile")
    desktop_raw = _run_one(page_key, "desktop")

    m = _extract(mobile_raw.get("lighthouseResult", {})) if mobile_raw else {}
    d = _extract(desktop_raw.get("lighthouseResult", {})) if desktop_raw else {}

    # CWV reported from the mobile run (mobile-first brand).
    base = m or d or {}
    image_ratio = (base.get("image_kb", 0) / base.get("total_kb", 1)) if base.get("total_kb") else 0

    return {
        "page": page_key,
        "name": config.page_name(page_key),
        "mobile_score": m.get("score", 0),
        "desktop_score": d.get("score", 0),
        "lcp_ms": round(base.get("lcp_ms", 0)),
        "cls": round(base.get("cls", 0), 3),
        "inp_ms": round(base.get("inp_ms", 0)),
        "ttfb_ms": round(base.get("ttfb_ms", 0)),
        "total_kb": base.get("total_kb", 0),
        "image_kb": base.get("image_kb", 0),
        "image_ratio": round(image_ratio, 2),
        "top_issues": base.get("top_issues", []),
        "weight_flag": base.get("total_kb", 0) > config.PAGE_WEIGHT_FLAG_KB,
        "image_flag": image_ratio > config.IMAGE_WEIGHT_FLAG_RATIO,
    }


def measure_all() -> list[dict]:
    results = []
    for key in config.PERF_PAGES:
        print(f"[pagespeed] measuring {key} …")
        results.append(measure_page(key))
    return results
