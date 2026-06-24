"""
GOODMONK D2C COMMAND CENTER
config.py — single source of truth for targets, thresholds, secrets.

Everything sensitive is read from environment variables so the same code runs
on Windows 11 (local .env), GitHub Actions (repo secrets) and any server.
Nothing secret is ever hard-coded here.
"""

import os
from pathlib import Path

# Optional: load a local .env when running on a developer machine.
# (GitHub Actions injects secrets directly, so python-dotenv is optional there.)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# --------------------------------------------------------------------------- #
#  PATHS
# --------------------------------------------------------------------------- #
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "dashboard" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
#  SITE UNDER TEST
# --------------------------------------------------------------------------- #
# Override BASE_URL with an env var to point the whole tool at staging/prod.
BASE_URL = os.getenv("GM_BASE_URL", "https://goodmonk.in").rstrip("/")

# The "robot customer" journey + the performance target list.
# `key` is a stable id used everywhere (sheets, json, dashboard).
# Edit the `path` values to match the real GoodMonk URLs.
PAGES = [
    {"key": "homepage",   "name": "Homepage",              "path": "/"},
    {"key": "collection", "name": "Collection / Shop",     "path": "/collections/all"},
    {"key": "h50",        "name": "H50+ PDP",              "path": "/products/h50-plus"},
    {"key": "fnm",        "name": "Family Nutrition Mix",  "path": "/products/family-nutrition-mix"},
    {"key": "protein",    "name": "Protein PDP",           "path": "/products/protein"},
    {"key": "checkout",   "name": "Checkout",              "path": "/checkout"},
]

# Pages PageSpeed Insights runs against (all of the above).
PERF_PAGES = [p["key"] for p in PAGES]

# Pages the robot-customer break-detection journey walks, in order.
# (Add-to-cart + checkout initiation are simulated as interaction steps.)
JOURNEY_PAGE_KEYS = ["homepage", "collection", "h50", "fnm"]

# The full 6-step journey with canonical display names, used by the break grid,
# the Website-Health table and the logger so every layer agrees on naming.
# "atc" and "checkout_init" are interaction steps, not perf target pages.
JOURNEY_STEPS = [
    ("homepage",      "Homepage"),
    ("collection",    "Collection / Shop"),
    ("h50",           "H50+ PDP"),
    ("fnm",           "Family Nutrition Mix"),
    ("atc",           "Add to Cart"),
    ("checkout_init", "Checkout Init"),
]
_STEP_NAMES = dict(JOURNEY_STEPS)

# Test SKU / selectors used by the Playwright add-to-cart step.
# Adjust these CSS selectors to the live theme. The journey never completes a
# real payment — it stops at checkout initiation.
ADD_TO_CART_SELECTOR = os.getenv("GM_ATC_SELECTOR", "button[name='add'], form[action*='/cart/add'] button[type='submit']")
CART_CHECKOUT_SELECTOR = os.getenv("GM_CHECKOUT_SELECTOR", "button[name='checkout'], a[href*='/checkout']")


def url_for(key: str) -> str:
    for p in PAGES:
        if p["key"] == key:
            return BASE_URL + p["path"]
    raise KeyError(f"Unknown page key: {key}")


def page_name(key: str) -> str:
    for p in PAGES:
        if p["key"] == key:
            return p["name"]
    return _STEP_NAMES.get(key, key)


# --------------------------------------------------------------------------- #
#  THRESHOLDS  (straight from the brief)
# --------------------------------------------------------------------------- #
# Load-time tiers, in seconds.
LOAD_HEALTHY_MAX = 4.0     # < 4s   -> Healthy
LOAD_WARNING_MAX = 10.0    # 4-10s  -> Warning ; > 10s -> Critical

# A step must fail this many checks in a row before add-to-cart / checkout
# escalate to Critical (the brief's "2 checks in a row" rule).
CONSECUTIVE_FAILS_FOR_CRITICAL = 2

# Core Web Vitals "good" benchmarks (brief table).
CWV_BENCHMARKS = {
    "lcp_ms":  2500,   # < 2.5s
    "cls":     0.10,   # < 0.1
    "inp_ms":  200,    # < 200ms
    "ttfb_ms": 800,    # < 0.8s
}

# Page-weight flags.
PAGE_WEIGHT_FLAG_KB = 2560        # > 2.5MB on mobile
IMAGE_WEIGHT_FLAG_RATIO = 0.60    # images > 60% of total weight

# A PageSpeed mobile score at/below this is treated as a critical perf issue.
PERF_CRITICAL_MOBILE_SCORE = 50


# --------------------------------------------------------------------------- #
#  GOOGLE PAGESPEED INSIGHTS
# --------------------------------------------------------------------------- #
PAGESPEED_API_KEY = os.getenv("PAGESPEED_API_KEY", "")
PAGESPEED_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


# --------------------------------------------------------------------------- #
#  GOOGLE SHEETS LOGGING
# --------------------------------------------------------------------------- #
# Service-account JSON is passed as a single env var (the file contents) so it
# can live in a GitHub secret. Sheet is identified by its ID.
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

SHEET_TABS = {
    "health": "Health_Checks",
    "performance": "Performance",
    "alerts": "Alerts",
}

SHEET_HEADERS = {
    "health": ["Timestamp", "Page", "Status", "HTTP_Code", "Load_Time", "Severity"],
    "performance": ["Timestamp", "Page", "Mobile_Score", "Desktop_Score",
                    "LCP", "CLS", "INP", "TTFB"],
    "alerts": ["Timestamp", "Severity", "Issue", "Status"],
}


# --------------------------------------------------------------------------- #
#  EMAIL ALERTS (Gmail SMTP)
# --------------------------------------------------------------------------- #
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
GMAIL_USER = os.getenv("GMAIL_USER", "")          # the sending Gmail address
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")  # 16-char app password
ALERT_RECIPIENTS = [e.strip() for e in os.getenv("ALERT_RECIPIENTS", "").split(",") if e.strip()]

# De-duplication window: don't re-send the same critical alert more often than
# this many minutes (prevents alert fatigue — the brief's #1 concern).
ALERT_DEDUP_MINUTES = int(os.getenv("ALERT_DEDUP_MINUTES", "120"))


def require(value: str, name: str) -> str:
    if not value:
        raise RuntimeError(
            f"Missing required configuration: {name}. "
            f"Set it as an environment variable / GitHub secret."
        )
    return value
