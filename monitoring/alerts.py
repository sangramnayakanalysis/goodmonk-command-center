"""
alerts.py — 3-tier-aware alerting with de-duplication + recovery messages.

The brief stresses that alert fatigue is the #1 reason monitoring tools get
ignored, so this module:
  * groups repeated failures of the same issue into ONE alert per dedup window,
  * sends a clear "RESOLVED" email when an issue recovers,
  * tags every alert with a tier (Email is the implemented free channel; the
    WhatsApp / Twilio tiers are stubbed with clear hooks because they are not
    free tools).

State is kept in dashboard/data/alert_state.json so it survives between
GitHub Actions runs (the workflow commits the data dir back to the repo).
"""

import json
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import config

STATE_FILE = config.DATA_DIR / "alert_state.json"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"open": {}}  # issue_key -> {first_seen, last_sent, count, severity, page}


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
#  EMAIL TRANSPORT (Gmail SMTP)
# --------------------------------------------------------------------------- #
def _send_email(subject: str, body: str) -> bool:
    if not (config.GMAIL_USER and config.GMAIL_APP_PASSWORD and config.ALERT_RECIPIENTS):
        print(f"[alerts] Email not configured — would have sent:\n  {subject}\n{body}\n")
        return False
    msg = MIMEMultipart()
    msg["From"] = f"GoodMonk Command Center <{config.GMAIL_USER}>"
    msg["To"] = ", ".join(config.ALERT_RECIPIENTS)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.starttls()
            server.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
            server.sendmail(config.GMAIL_USER, config.ALERT_RECIPIENTS, msg.as_string())
        print(f"[alerts] Email sent: {subject}")
        return True
    except Exception as exc:
        print(f"[alerts] Email send FAILED: {exc}")
        return False


def _whatsapp_stub(text: str):
    # Tier 2 hook. WhatsApp Cloud API / Twilio are not free; wire here if added.
    print(f"[alerts][tier2-whatsapp-stub] {text}")


def _phone_stub(text: str):
    # Tier 1 hook (Twilio Voice) — reserve for sustained revenue-bleeding events.
    print(f"[alerts][tier1-phone-stub] {text}")


# --------------------------------------------------------------------------- #
#  PUBLIC API
# --------------------------------------------------------------------------- #
RECOMMENDED_ACTIONS = {
    "homepage_down": "Check Shopify status + theme deploy; confirm DNS/CDN; escalate to dev on-call.",
    "checkout_failed": "Verify Shopflo/Shopify checkout, payment gateway and recent app changes immediately.",
    "atc_failed": "Inspect product availability, cart endpoint (/cart/add) and recent theme changes.",
    "http_error": "Inspect server/app logs for the failing route; check recent deploys.",
    "slow_page": "Review PageSpeed diagnostics for this page; likely heavy image or render-blocking JS.",
    "perf_critical": "Open monthly diagnostics; address the top diagnosed cause for this page.",
}


class AlertManager:
    """Tracks open issues across runs; emits one alert per dedup window and a
    recovery message when an issue clears."""

    def __init__(self, sheet_logger=None):
        self.state = _load_state()
        self.sheet = sheet_logger
        self.dedup = dt.timedelta(minutes=config.ALERT_DEDUP_MINUTES)

    # issue_key is a stable identifier, e.g. "homepage_down" or "slow_page:h50"
    def raise_issue(self, issue_key: str, severity: str, page: str,
                    detail: str, action_key: str):
        now = _now()
        existing = self.state["open"].get(issue_key)
        action = RECOMMENDED_ACTIONS.get(action_key, "Investigate and resolve.")

        if existing:
            existing["count"] += 1
            existing["last_seen"] = now.isoformat()
            last_sent = dt.datetime.fromisoformat(existing["last_sent"])
            if now - last_sent < self.dedup:
                # Within dedup window: stay quiet, just keep counting.
                _save_state(self.state)
                return
            # Sustained issue past the window -> escalation note (re-send).
            mins = int((now - dt.datetime.fromisoformat(existing["first_seen"])).total_seconds() // 60)
            self._dispatch(severity, page, detail, action,
                           escalation=f"Still failing after {mins} min "
                                      f"({existing['count']} checks).")
            existing["last_sent"] = now.isoformat()
        else:
            self.state["open"][issue_key] = {
                "first_seen": now.isoformat(),
                "last_seen": now.isoformat(),
                "last_sent": now.isoformat(),
                "count": 1,
                "severity": severity,
                "page": page,
                "detail": detail,
            }
            self._dispatch(severity, page, detail, action, escalation=None)
            if self.sheet:
                self.sheet.log_alert(severity, f"{page}: {detail}", status="Open")
        _save_state(self.state)

    def clear_issue(self, issue_key: str):
        """Mark an issue resolved and send a recovery message (once)."""
        existing = self.state["open"].pop(issue_key, None)
        if existing:
            page = existing.get("page", "")
            detail = existing.get("detail", issue_key)
            self._dispatch_recovery(page, detail)
            if self.sheet:
                self.sheet.log_alert("Resolved", f"{page}: {detail}", status="Resolved")
            _save_state(self.state)

    def reconcile(self, active_issue_keys: set):
        """Any previously-open issue not in active_issue_keys has recovered."""
        for key in list(self.state["open"].keys()):
            if key not in active_issue_keys:
                self.clear_issue(key)

    def open_issue_keys(self) -> set:
        return set(self.state["open"].keys())

    # ------------------------------------------------------------------ #
    def _dispatch(self, severity, page, detail, action, escalation):
        when = _now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        subject = "GOODMONK CRITICAL ALERT" if severity == "Critical" else f"GoodMonk {severity}"
        lines = [
            f"Severity      : {severity}",
            f"Issue         : {detail}",
            f"Affected Page : {page}",
            f"Time          : {when}",
            f"Action        : {action}",
        ]
        if escalation:
            lines.insert(1, f"Escalation    : {escalation}")
        body = "\n".join(lines) + "\n\n— GoodMonk D2C Command Center"
        _send_email(subject, body)
        if severity == "Critical":
            _whatsapp_stub(f"{severity}: {page} — {detail}")
            if escalation:  # sustained -> tier 1
                _phone_stub(f"SUSTAINED {page} — {detail}")

    def _dispatch_recovery(self, page, detail):
        when = _now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        body = (f"Status        : RESOLVED\n"
                f"Issue         : {detail}\n"
                f"Affected Page : {page}\n"
                f"Recovered     : {when}\n\n— GoodMonk D2C Command Center")
        _send_email("GoodMonk RESOLVED — service recovered", body)
        _whatsapp_stub(f"RESOLVED: {page} — {detail}")
