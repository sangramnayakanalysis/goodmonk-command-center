"""
run_performance.py — System 2 orchestrator: Performance Benchmarking & Diagnostics.

Runs PageSpeed Insights against every target page, logs Core Web Vitals to the
Performance sheet, and raises a (non-paging) Critical alert only when a page's
mobile score collapses. Writes the latest perf snapshot to dashboard/data so the
JSON generator and dashboard can read it without re-querying PSI.
"""

import json
import datetime as dt

import config
import pagespeed
from sheet_logger import SheetLogger
from alerts import AlertManager

SNAPSHOT_FILE = config.DATA_DIR / "perf_snapshot.json"


def run() -> dict:
    sheet = SheetLogger()
    alerts = AlertManager(sheet_logger=sheet)
    results = pagespeed.measure_all()
    active_perf_issues = set()

    for r in results:
        sheet.log_performance(
            r["page"], r["mobile_score"], r["desktop_score"],
            r["lcp_ms"], r["cls"], r["inp_ms"], r["ttfb_ms"],
        )
        # Perf is rarely urgent — but a collapsed mobile score is worth flagging.
        if r["mobile_score"] and r["mobile_score"] <= config.PERF_CRITICAL_MOBILE_SCORE:
            ikey = f"perf_critical:{r['page']}"
            active_perf_issues.add(ikey)
            top = r["top_issues"][0] if r["top_issues"] else "see diagnostics"
            alerts.raise_issue(
                ikey, "Critical", r["name"],
                f"Mobile PageSpeed {r['mobile_score']}/100 — {top}",
                "perf_critical",
            )

    # Only reconcile perf issues here (don't touch break-detection issues).
    for key in list(alerts.open_issue_keys()):
        if key.startswith("perf_critical:") and key not in active_perf_issues:
            alerts.clear_issue(key)

    snapshot = {
        "ran_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "pages": results,
    }
    SNAPSHOT_FILE.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"[perf] wrote snapshot for {len(results)} pages.")
    return snapshot


if __name__ == "__main__":
    run()
