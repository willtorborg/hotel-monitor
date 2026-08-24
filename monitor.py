#!/usr/bin/env python3
"""Hotel availability monitor — Courtyard Philadelphia Devon/Villanova.

Data source: Xotelo (https://xotelo.com), a free keyless JSON API over
TripAdvisor's meta-search rates. For this hotel the feed includes Marriott's
own official rate (code "Marriott1"), plus OTA channels. Sold out for the
dates -> the rates list comes back empty.

False-negative guard: every run also queries a rolling CONTROL date ~6 weeks
out at the same hotel. If the control date returns rates while the target
dates return none, the sold-out reading is trusted (UNAVAILABLE). If both
come back empty, the feed itself is likely broken -> UNKNOWN, never a silent
false "no rooms".

States: AVAILABLE, UNAVAILABLE, UNKNOWN.

Usage:
  python monitor.py               # check, alert on transition, update state.json
  python monitor.py --dry-run     # print the detected state; send nothing, write nothing
  python monitor.py --force-alert # send a [TEST] alert through every configured channel
"""

import argparse
import json
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG — edit here to point at a different hotel or dates
# ---------------------------------------------------------------------------
CONFIG = {
    "hotel_name": "Courtyard by Marriott Philadelphia Devon/Villanova",
    # TripAdvisor location key for the hotel (from its tripadvisor.com URL:
    # .../Hotel_Review-g53933-d96877-... -> "g53933-d96877").
    "hotel_key": "g53933-d96877",
    "check_in": "2026-09-25",   # YYYY-MM-DD
    "check_out": "2026-09-27",  # YYYY-MM-DD
    "rooms": 1,
    "adults": 2,
    # Where the alert sends you to book (marriott.com with dates pre-filled).
    "booking_url": (
        "https://www.marriott.com/reservation/availabilitySearch.mi?"
        "propertyCode=PHLDV&fromDate=09%2F25%2F2026&toDate=09%2F27%2F2026"
        "&lengthOfStay=2&numberOfRooms=1&numberOfAdults=2&childrenCount=0"
        "&clusterCode=none&isHwsGroupSearch=true&useRewardsPoints=false"
        "&flexibleDateSearch=false"
    ),
    "alert_subject": "ROOM OPEN: Courtyard Devon 9/25-9/27",
}

XOTELO_URL = "https://data.xotelo.com/api/rates"


def ssl_context():
    """Use certifi's CA bundle when available (macOS system Python often
    lacks usable certs); the standard trust store otherwise."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()
ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
UNKNOWN_STREAK_ALERT_AT = 4  # ~1 hour at a 15-minute cadence


def env(name, required=True):
    v = os.environ.get(name, "").strip()
    if required and not v:
        raise SystemExit(f"Missing required env var / secret: {name}")
    return v


def fetch_rates(check_in, check_out):
    """One Xotelo query. Returns (rates_list_or_None, detail). None => error."""
    params = urllib.parse.urlencode({
        "hotel_key": CONFIG["hotel_key"],
        "chk_in": check_in,
        "chk_out": check_out,
        "adults": CONFIG["adults"],
        "rooms": CONFIG["rooms"],
    })
    req = urllib.request.Request(f"{XOTELO_URL}?{params}",
                                 headers={"User-Agent": "hotel-monitor/1.0 (personal availability check)"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as r:
            body = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return None, f"request failed: {e}"
    if body.get("error"):
        return None, f"API error: {body['error']}"
    result = body.get("result") or {}
    rates = result.get("rates")
    if rates is None:
        return None, f"malformed response: {json.dumps(body)[:200]}"
    return rates, "ok"


def control_dates():
    """A rolling midweek 1-night stay ~6 weeks out, used to prove the feed is live."""
    d = datetime.now(timezone.utc).date() + timedelta(days=42)
    d += timedelta(days=(1 - d.weekday()) % 7)  # next Tuesday on/after that
    return d.isoformat(), (d + timedelta(days=1)).isoformat()


def check_availability():
    """Returns {state, detail, rate}."""
    rates, detail = fetch_rates(CONFIG["check_in"], CONFIG["check_out"])
    if rates is None:
        return {"state": "UNKNOWN", "detail": detail}

    if rates:
        best = min(rates, key=lambda r: r.get("rate") or 1e9)
        marriott = next((r for r in rates if "marriott" in (r.get("code") or "").lower()
                         or "courtyard" in (r.get("name") or "").lower()), None)
        shown = marriott or best
        others = ", ".join(f"{r.get('name')} ${r.get('rate')}" for r in rates if r is not shown)
        return {"state": "AVAILABLE",
                "rate": f"{shown.get('name')} ${shown.get('rate')}/night",
                "detail": f"{len(rates)} channel(s) selling" + (f" (also: {others})" if others else "")}

    # Empty rates: verify the feed itself is alive before trusting "sold out".
    ctrl_in, ctrl_out = control_dates()
    ctrl_rates, ctrl_detail = fetch_rates(ctrl_in, ctrl_out)
    if ctrl_rates:
        return {"state": "UNAVAILABLE",
                "detail": f"no rates for target dates (feed verified live via control {ctrl_in})"}
    if ctrl_rates is None:
        return {"state": "UNKNOWN", "detail": f"target empty and control errored: {ctrl_detail}"}
    return {"state": "UNKNOWN",
            "detail": f"target AND control ({ctrl_in}) both empty — feed may be broken"}


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def send_email(subject, body, to_addrs):
    addr, pwd = env("GMAIL_ADDRESS"), env("GMAIL_APP_PASSWORD")
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = addr
    msg["To"] = ", ".join(to_addrs)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(addr, pwd)
        s.sendmail(addr, to_addrs, msg.as_string())


def send_ntfy(title, body, priority="urgent"):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=body.encode(),
        headers={"Title": title, "Priority": priority,
                 "Click": CONFIG["booking_url"], "Tags": "hotel,bell"})
    urllib.request.urlopen(req, timeout=15, context=ssl_context())


def alert_targets():
    targets = [env("ALERT_EMAIL")]
    sms = os.environ.get("ALERT_SMS_EMAIL", "").strip()
    if sms:
        targets.append(sms)
    return targets


def send_available_alert(result, test=False):
    c = CONFIG
    prefix = "[TEST] " if test else ""
    body = (f"{prefix}{c['hotel_name']}\n"
            f"{c['check_in']} -> {c['check_out']}, {c['rooms']} room, {c['adults']} adults\n\n"
            f"{result['state']}"
            + (f" — {result.get('rate')}" if result.get("rate") else "")
            + f"\n{result['detail']}\n\nBOOK NOW:\n{c['booking_url']}\n")
    send_email(prefix + c["alert_subject"], body, alert_targets())
    send_ntfy(prefix + c["alert_subject"], "Book now! " + c["booking_url"])


def send_broken_alert(state):
    body = (f"The hotel monitor has returned UNKNOWN for {state['unknown_streak']} consecutive runs "
            f"(~{state['unknown_streak'] * 15} min). The data feed may be down.\n\n"
            f"Last detail: {state.get('last_detail', '?')}")
    send_email("Hotel monitor may be broken", body, [env("ALERT_EMAIL")])


# ---------------------------------------------------------------------------
# State + main
# ---------------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_state": None, "state_since": None, "unknown_streak": 0,
            "blocked_alert_sent": False}


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-alert", action="store_true")
    args = ap.parse_args()

    if args.force_alert:
        send_available_alert({"state": "AVAILABLE", "rate": "Courtyard $233/night",
                              "detail": "forced test"}, test=True)
        print("Test alert sent to", alert_targets())
        return

    result = check_availability()
    print(f"[{now_utc()}] {result['state']} — {result['detail']}"
          + (f" — {result['rate']}" if result.get("rate") else ""))

    if args.dry_run:
        print("(dry run: no alerts sent, no state written)")
        return

    state = load_state()
    state["last_detail"] = result["detail"]

    if result["state"] != state.get("last_state") and result["state"] != "UNKNOWN":
        state["state_since"] = now_utc()

    # Alert on the transition into AVAILABLE; re-arms whenever it goes
    # UNAVAILABLE again afterwards.
    if result["state"] == "AVAILABLE" and state.get("last_state") != "AVAILABLE":
        send_available_alert(result)
        print("ALERT SENT")

    if result["state"] == "UNKNOWN":
        state["unknown_streak"] = state.get("unknown_streak", 0) + 1
        if state["unknown_streak"] == UNKNOWN_STREAK_ALERT_AT and not state.get("blocked_alert_sent"):
            send_broken_alert(state)
            state["blocked_alert_sent"] = True
    else:
        state["unknown_streak"] = 0
        state["blocked_alert_sent"] = False
        state["last_state"] = result["state"]

    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main()
