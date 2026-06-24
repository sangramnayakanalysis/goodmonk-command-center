"""
json_generator.py — turns raw history (Google Sheet + local snapshots) into the
static JSON files the InfinityFree dashboard reads.

Outputs (all under dashboard/data/):
    dashboard.json   executive summary + health table + break-detection grid
    performance.json latest Core Web Vitals per page + flags
    alerts.json      open / resolved / critical alert lists
    trends.json      time-series for the 4 trend charts
    weekly.json      best/worst/fastest/slowest + incident count
    monthly.json     ranked diagnostics: top issues, impact, recommended fix

The dashboard never talks to Google or PSI directly — it only fetches these
files, so it stays fast and InfinityFree-friendly.
"""

import json
import datetime as dt
from collections import defaultdict

import config
from sheet_logger import SheetLogger

OUT = config.DATA_DIR


def _write(name: str, obj):
    (OUT / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[json] wrote {name}")


def _parse_ts(s: str):
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def _load_perf_snapshot() -> dict:
    f = OUT / "perf_snapshot.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"pages": [], "ran_at": None}


def _severity_of(load_time, status):
    if status == "FAILED":
        return "Critical"
    if status == "WARNING":
        return "Warning"
    return "Healthy"


def build():
    sheet = SheetLogger()
    health_rows = sheet.read_all("health")
    perf_rows = sheet.read_all("performance")
    alert_rows = sheet.read_all("alerts")
    perf_snap = _load_perf_snapshot()
    now = dt.datetime.now(dt.timezone.utc).astimezone()

    # ---------------------------------------------------------------- #
    # Latest health status per page (last row wins).
    # ---------------------------------------------------------------- #
    latest_health = {}
    for row in health_rows:
        latest_health[row.get("Page")] = row

    health_table = []
    for key, name in config.JOURNEY_STEPS:
        row = latest_health.get(name)
        if row:
            try:
                lt = float(row.get("Load_Time", 0) or 0)
            except ValueError:
                lt = 0.0
            health_table.append({
                "page": name,
                "status": row.get("Status", "—"),
                "http": row.get("HTTP_Code", "—"),
                "load_time": lt,
                "severity": row.get("Severity", "—"),
                "last_checked": row.get("Timestamp", "—"),
            })

    # Break-detection grid (the 6 critical journey steps).
    break_grid = []
    for key, name in config.JOURNEY_STEPS:
        row = latest_health.get(name)
        label = {"Collection / Shop": "Collection", "H50+ PDP": "H50",
                 "Family Nutrition Mix": "FNM", "Checkout Init": "Checkout"}.get(name, name)
        break_grid.append({
            "label": label,
            "status": (row.get("Status") if row else "—"),
        })

    # Executive summary.
    sev_counts = defaultdict(int)
    load_times = []
    for h in health_table:
        sev_counts[h["severity"]] += 1
        if h["load_time"]:
            load_times.append(h["load_time"])
    total = len(health_table)
    healthy = sev_counts["Healthy"]
    warning = sev_counts["Warning"]
    critical = sev_counts["Critical"]
    avg_load = round(sum(load_times) / len(load_times), 2) if load_times else 0
    health_score = round(100 * healthy / total) if total else 0
    site_status = "DOWN" if any(b["label"] == "Homepage" and b["status"] == "FAILED"
                                for b in break_grid) else \
                  ("DEGRADED" if critical or warning else "OPERATIONAL")

    dashboard = {
        "generated_at": now.isoformat(timespec="seconds"),
        "site": config.BASE_URL,
        "summary": {
            "site_status": site_status,
            "pages_monitored": total,
            "healthy": healthy,
            "warning": warning,
            "critical": critical,
            "avg_load_time": avg_load,
            "health_score": health_score,
        },
        "health_table": health_table,
        "break_grid": break_grid,
    }
    _write("dashboard.json", dashboard)

    # ---------------------------------------------------------------- #
    # Performance (latest snapshot preferred; fall back to sheet).
    # ---------------------------------------------------------------- #
    perf_pages = perf_snap.get("pages") or []
    if not perf_pages and perf_rows:
        latest_perf = {}
        for row in perf_rows:
            latest_perf[row.get("Page")] = row
        for p in config.PAGES:
            row = latest_perf.get(p["name"])
            if not row:
                continue
            perf_pages.append({
                "page": p["key"], "name": p["name"],
                "mobile_score": row.get("Mobile_Score", 0),
                "desktop_score": row.get("Desktop_Score", 0),
                "lcp_ms": float(row.get("LCP", 0) or 0) * 1000,
                "cls": float(row.get("CLS", 0) or 0),
                "inp_ms": float(row.get("INP", 0) or 0),
                "ttfb_ms": float(row.get("TTFB", 0) or 0) * 1000,
                "top_issues": [],
            })
    _write("performance.json", {
        "generated_at": now.isoformat(timespec="seconds"),
        "benchmarks": config.CWV_BENCHMARKS,
        "pages": perf_pages,
    })

    # ---------------------------------------------------------------- #
    # Alerts.
    # ---------------------------------------------------------------- #
    open_alerts, resolved_alerts = [], []
    for row in alert_rows:
        item = {
            "time": row.get("Timestamp"),
            "severity": row.get("Severity"),
            "issue": row.get("Issue"),
            "status": row.get("Status"),
        }
        if str(row.get("Status", "")).lower() == "resolved":
            resolved_alerts.append(item)
        else:
            open_alerts.append(item)
    _write("alerts.json", {
        "generated_at": now.isoformat(timespec="seconds"),
        "open": open_alerts[-50:][::-1],
        "resolved": resolved_alerts[-50:][::-1],
        "critical_count": sum(1 for a in open_alerts if a["severity"] == "Critical"),
    })

    # ---------------------------------------------------------------- #
    # Trends (downsample history into chart series).
    # ---------------------------------------------------------------- #
    health_trend = defaultdict(lambda: {"healthy": 0, "warning": 0, "critical": 0})
    load_trend = defaultdict(list)
    for row in health_rows:
        ts = _parse_ts(row.get("Timestamp", ""))
        if not ts:
            continue
        bucket = ts.strftime("%Y-%m-%d %H:00")
        sev = row.get("Severity", "Healthy").lower()
        if sev in ("healthy", "warning", "critical"):
            health_trend[bucket][sev] += 1
        try:
            load_trend[bucket].append(float(row.get("Load_Time", 0) or 0))
        except ValueError:
            pass

    perf_trend = defaultdict(list)
    for row in perf_rows:
        ts = _parse_ts(row.get("Timestamp", ""))
        if not ts:
            continue
        bucket = ts.strftime("%Y-%m-%d %H:00")
        try:
            perf_trend[bucket].append(float(row.get("Mobile_Score", 0) or 0))
        except ValueError:
            pass

    alert_trend = defaultdict(int)
    for row in alert_rows:
        ts = _parse_ts(row.get("Timestamp", ""))
        if ts and str(row.get("Status", "")).lower() != "resolved":
            alert_trend[ts.strftime("%Y-%m-%d")] += 1

    h_labels = sorted(health_trend.keys())[-48:]
    p_labels = sorted(perf_trend.keys())[-48:]
    a_labels = sorted(alert_trend.keys())[-30:]
    _write("trends.json", {
        "generated_at": now.isoformat(timespec="seconds"),
        "health": {
            "labels": h_labels,
            "healthy": [health_trend[l]["healthy"] for l in h_labels],
            "warning": [health_trend[l]["warning"] for l in h_labels],
            "critical": [health_trend[l]["critical"] for l in h_labels],
        },
        "load_time": {
            "labels": h_labels,
            "avg": [round(sum(load_trend[l]) / len(load_trend[l]), 2) if load_trend[l] else 0
                    for l in h_labels],
        },
        "performance": {
            "labels": p_labels,
            "mobile_avg": [round(sum(perf_trend[l]) / len(perf_trend[l])) if perf_trend[l] else 0
                           for l in p_labels],
        },
        "alerts": {
            "labels": a_labels,
            "count": [alert_trend[l] for l in a_labels],
        },
    })

    # ---------------------------------------------------------------- #
    # Weekly summary (last 7 days of perf + health).
    # ---------------------------------------------------------------- #
    week_ago = now - dt.timedelta(days=7)
    page_scores = defaultdict(list)
    for row in perf_rows:
        ts = _parse_ts(row.get("Timestamp", ""))
        if ts and ts >= week_ago:
            try:
                page_scores[row.get("Page")].append(float(row.get("Mobile_Score", 0) or 0))
            except ValueError:
                pass
    avg_score = {p: sum(v) / len(v) for p, v in page_scores.items() if v}

    page_loads = defaultdict(list)
    incidents = 0
    for row in health_rows:
        ts = _parse_ts(row.get("Timestamp", ""))
        if ts and ts >= week_ago:
            try:
                page_loads[row.get("Page")].append(float(row.get("Load_Time", 0) or 0))
            except ValueError:
                pass
            if row.get("Severity") == "Critical":
                incidents += 1
    avg_load_by_page = {p: sum(v) / len(v) for p, v in page_loads.items() if v}

    def _pick(d, best=True):
        if not d:
            return {"page": "—", "value": 0}
        k = (max if best else min)(d, key=d.get)
        return {"page": k, "value": round(d[k], 2)}

    _write("weekly.json", {
        "generated_at": now.isoformat(timespec="seconds"),
        "best_page": _pick(avg_score, best=True),
        "worst_page": _pick(avg_score, best=False),
        "fastest_page": _pick(avg_load_by_page, best=False),
        "slowest_page": _pick(avg_load_by_page, best=True),
        "incidents": incidents,
    })

    # ---------------------------------------------------------------- #
    # Monthly diagnostics (ranked top issues from latest perf snapshot).
    # ---------------------------------------------------------------- #
    diagnostics = []
    for pg in perf_pages:
        for issue in pg.get("top_issues", []):
            impact = "High" if (pg.get("mobile_score", 100) <= 50) else \
                     ("Medium" if pg.get("mobile_score", 100) <= 75 else "Low")
            diagnostics.append({
                "page": pg.get("name"),
                "issue": issue,
                "impact": impact,
                "fix": _suggest_fix(issue),
                "mobile_score": pg.get("mobile_score", 0),
            })
    impact_rank = {"High": 0, "Medium": 1, "Low": 2}
    diagnostics.sort(key=lambda d: (impact_rank.get(d["impact"], 3), d["mobile_score"]))
    _write("monthly.json", {
        "generated_at": now.isoformat(timespec="seconds"),
        "diagnostics": diagnostics[:20],
    })

    print("[json] all dashboard data generated.")


def _suggest_fix(issue: str) -> str:
    s = issue.lower()
    if "next-gen" in s or "webp" in s or "format" in s:
        return "Serve WebP/AVIF and lazy-load below-the-fold images."
    if "compress" in s or "optimi" in s or "image" in s:
        return "Compress and correctly size hero/product images; add width/height."
    if "render-blocking" in s:
        return "Defer non-critical CSS/JS; inline critical CSS."
    if "unused javascript" in s:
        return "Code-split and remove unused app/theme JS bundles."
    if "unused css" in s:
        return "Purge unused theme CSS; split per-template stylesheets."
    if "server response" in s or "ttfb" in s:
        return "Improve Shopify app/theme server time; review heavy Liquid loops."
    if "page weight" in s:
        return "Reduce total transferred bytes; audit third-party scripts."
    return "Review the PageSpeed opportunity detail for this page."


if __name__ == "__main__":
    build()
