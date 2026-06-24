"""
sheet_logger.py — append rows to the GoodMonk Google Sheet and read recent
state back (needed for de-duplication, recovery messages and trend JSON).

Auth: a Google service account. Share the target Sheet with the service
account's email (Editor) and the tool can read/write it.

If Sheets is not configured the logger degrades gracefully to local JSONL
files under dashboard/data/ so the rest of the pipeline still runs.
"""

import json
import datetime as dt
from pathlib import Path

import config

_LOCAL_FALLBACK = config.DATA_DIR / "_sheet_fallback"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


class SheetLogger:
    def __init__(self):
        self.enabled = bool(config.GOOGLE_SHEET_ID and config.GOOGLE_SERVICE_ACCOUNT_JSON)
        self._sh = None
        if self.enabled:
            self._connect()
        else:
            _LOCAL_FALLBACK.mkdir(parents=True, exist_ok=True)
            print("[sheet_logger] Google Sheet not configured — using local fallback files.")

    # ------------------------------------------------------------------ #
    def _connect(self):
        import gspread
        from google.oauth2.service_account import Credentials

        info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        gc = gspread.authorize(creds)
        self._sh = gc.open_by_key(config.GOOGLE_SHEET_ID)
        self._ensure_tabs()

    def _ensure_tabs(self):
        existing = {ws.title: ws for ws in self._sh.worksheets()}
        for tab_key, title in config.SHEET_TABS.items():
            headers = config.SHEET_HEADERS[tab_key]
            if title not in existing:
                ws = self._sh.add_worksheet(title=title, rows=2000, cols=max(8, len(headers)))
                ws.append_row(headers, value_input_option="RAW")
            else:
                ws = existing[title]
                first = ws.row_values(1)
                if first != headers:
                    ws.update("A1", [headers])

    def _ws(self, tab_key: str):
        return self._sh.worksheet(config.SHEET_TABS[tab_key])

    # ------------------------------------------------------------------ #
    #  WRITE
    # ------------------------------------------------------------------ #
    def append(self, tab_key: str, row: list):
        if self.enabled:
            self._ws(tab_key).append_row(
                [str(c) for c in row], value_input_option="USER_ENTERED"
            )
        else:
            f = _LOCAL_FALLBACK / f"{tab_key}.jsonl"
            with f.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(zip(config.SHEET_HEADERS[tab_key], row))) + "\n")

    def log_health(self, page_key, status, http_code, load_time_s, severity):
        self.append("health", [
            _now_iso(), config.page_name(page_key), status,
            http_code, round(load_time_s, 3), severity,
        ])

    def log_performance(self, page_key, mobile, desktop, lcp_ms, cls, inp_ms, ttfb_ms):
        self.append("performance", [
            _now_iso(), config.page_name(page_key), mobile, desktop,
            round(lcp_ms / 1000.0, 2), round(cls, 3),
            int(inp_ms), round(ttfb_ms / 1000.0, 2),
        ])

    def log_alert(self, severity, issue, status="Open"):
        self.append("alerts", [_now_iso(), severity, issue, status])

    # ------------------------------------------------------------------ #
    #  READ  (for dedup / recovery / trends)
    # ------------------------------------------------------------------ #
    def read_all(self, tab_key: str) -> list[dict]:
        if self.enabled:
            try:
                return self._ws(tab_key).get_all_records()
            except Exception as exc:  # empty sheet etc.
                print(f"[sheet_logger] read warning: {exc}")
                return []
        f = _LOCAL_FALLBACK / f"{tab_key}.jsonl"
        if not f.exists():
            return []
        return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]

    def recent(self, tab_key: str, limit: int = 500) -> list[dict]:
        rows = self.read_all(tab_key)
        return rows[-limit:]
