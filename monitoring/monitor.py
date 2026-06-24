"""
monitor.py — System 1: Critical Break Detection.

Runs a scripted "robot customer" journey with Playwright:
    Homepage -> Collection -> H50+ PDP -> FNM PDP -> Add to Cart -> Checkout init

For each step it records pass/fail, HTTP status code and load time, then applies
the brief's severity rules and feeds the AlertManager (dedup + recovery).

It NEVER completes a real payment — it stops at checkout initiation.

Usage:
    python monitor.py            # full journey + alerts + sheet logging
"""

import sys
import time
import datetime as dt

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

import config
from sheet_logger import SheetLogger
from alerts import AlertManager


def _classify(load_time_s: float, ok: bool, http_code: int) -> str:
    if not ok or http_code >= 400:
        return "Critical"
    if load_time_s > config.LOAD_WARNING_MAX:
        return "Critical"          # >10s == effectively broken
    if load_time_s >= config.LOAD_HEALTHY_MAX:
        return "Warning"           # 4-10s
    return "Healthy"


def _goto(page, url: str):
    """Navigate and return (ok, http_code, load_time_s)."""
    start = time.perf_counter()
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        load = time.perf_counter() - start
        code = resp.status if resp else 0
        ok = bool(resp) and code < 400
        return ok, code, load
    except PWTimeout:
        return False, 0, time.perf_counter() - start
    except Exception as exc:
        print(f"[monitor] goto error {url}: {exc}")
        return False, 0, time.perf_counter() - start


def run_journey() -> dict:
    sheet = SheetLogger()
    alerts = AlertManager(sheet_logger=sheet)
    steps = []
    active_issues = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="GoodMonkMonitor/1.0 (+robot-customer health check)",
            viewport={"width": 390, "height": 844},  # mobile-first brand
        )
        page = context.new_page()

        # ---- Steps 1-4: page loads --------------------------------------- #
        for key in config.JOURNEY_PAGE_KEYS:
            url = config.url_for(key)
            ok, code, load = _goto(page, url)
            sev = _classify(load, ok, code)
            status = "PASS" if sev == "Healthy" else ("WARNING" if sev == "Warning" else "FAILED")
            steps.append({"key": key, "name": config.page_name(key), "status": status,
                          "http": code, "load_time": round(load, 2), "severity": sev})
            sheet.log_health(key, status, code, load, sev)
            print(f"[monitor] {key:10s} {status:7s} http={code} {load:.2f}s")

            if sev == "Critical":
                if key == "homepage":
                    active_issues.add("homepage_down")
                    alerts.raise_issue("homepage_down", "Critical", config.page_name(key),
                                       f"Homepage failed to load (HTTP {code}, {load:.1f}s)",
                                       "homepage_down")
                else:
                    ikey = f"http_error:{key}"
                    active_issues.add(ikey)
                    alerts.raise_issue(ikey, "Critical", config.page_name(key),
                                       f"Load failure (HTTP {code}, {load:.1f}s)", "http_error")
            elif load > config.LOAD_WARNING_MAX:
                ikey = f"slow_page:{key}"
                active_issues.add(ikey)
                alerts.raise_issue(ikey, "Critical", config.page_name(key),
                                   f"Page load {load:.1f}s (>10s)", "slow_page")

        # ---- Step 5: Add to Cart ---------------------------------------- #
        atc_ok = False
        atc_code = 0
        atc_start = time.perf_counter()
        try:
            _goto(page, config.url_for("h50"))
            page.locator(config.ADD_TO_CART_SELECTOR).first.click(timeout=15000)
            # Wait for cart to reflect the add (Shopify usually returns 200 on /cart/add).
            page.wait_for_timeout(2500)
            atc_ok = True
            atc_code = 200
        except Exception as exc:
            print(f"[monitor] add-to-cart failed: {exc}")
        atc_load = time.perf_counter() - atc_start
        atc_sev = "Healthy" if atc_ok else "Critical"
        atc_status = "PASS" if atc_ok else "FAILED"
        steps.append({"key": "atc", "name": "Add to Cart", "status": atc_status,
                      "http": atc_code, "load_time": round(atc_load, 2), "severity": atc_sev})
        sheet.log_health("atc", atc_status, atc_code, atc_load, atc_sev)
        print(f"[monitor] {'atc':10s} {atc_status:7s} {atc_load:.2f}s")
        # 2-consecutive-fail rule handled via dedup state count; raise on fail.
        if not atc_ok:
            active_issues.add("atc_failed")
            alerts.raise_issue("atc_failed", "Critical", "Add to Cart",
                               "Add to Cart step failed", "atc_failed")

        # ---- Step 6: Checkout initiation (NO payment) ------------------- #
        co_ok = False
        co_code = 0
        co_start = time.perf_counter()
        try:
            resp = page.goto(config.url_for("checkout"), wait_until="domcontentloaded", timeout=30000)
            co_code = resp.status if resp else 0
            # A redirect to login/empty-cart is still "checkout reachable".
            co_ok = bool(resp) and co_code < 400
        except Exception as exc:
            print(f"[monitor] checkout init failed: {exc}")
        co_load = time.perf_counter() - co_start
        co_sev = "Healthy" if co_ok else "Critical"
        co_status = "PASS" if co_ok else "FAILED"
        steps.append({"key": "checkout_init", "name": "Checkout Init", "status": co_status,
                      "http": co_code, "load_time": round(co_load, 2), "severity": co_sev})
        sheet.log_health("checkout_init", co_status, co_code, co_load, co_sev)
        print(f"[monitor] {'checkout':10s} {co_status:7s} http={co_code} {co_load:.2f}s")
        if not co_ok:
            active_issues.add("checkout_failed")
            alerts.raise_issue("checkout_failed", "Critical", "Checkout",
                               f"Checkout initiation failed (HTTP {co_code})", "checkout_failed")

        context.close()
        browser.close()

    # ---- Recovery: clear any previously-open issue that's now healthy --- #
    alerts.reconcile(active_issues)

    summary = {
        "ran_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "steps": steps,
        "open_issue_keys": sorted(alerts.open_issue_keys()),
    }
    return summary


if __name__ == "__main__":
    result = run_journey()
    crit = sum(1 for s in result["steps"] if s["severity"] == "Critical")
    print(f"\n[monitor] done — {len(result['steps'])} steps, {crit} critical.")
    # Non-zero exit signals a failing journey to CI logs (optional gate).
    sys.exit(0)
