# Hotel Availability Monitor

Watches the **Courtyard by Marriott Philadelphia Devon/Villanova** for
**Sep 25–27, 2026 · 1 room · 2 adults** and notifies you the moment rooms
become bookable. Runs on GitHub Actions every ~15 minutes. No paid services,
no API signups.

## How it works

Each run makes one JSON request to [Xotelo](https://xotelo.com) — a free,
keyless API over TripAdvisor's meta-search rates. For this hotel the feed
includes **Marriott's own official rate** (it matches marriott.com to the
dollar) plus OTA channels like Trip.com.

- Any channel selling → **AVAILABLE** → immediate email + phone push with a
  marriott.com booking link, dates pre-filled. Sent once per transition;
  re-arms if the hotel sells out again.
- Empty rate list → the run first re-queries a rolling **control date**
  (~6 weeks out, midweek) at the same hotel. Control has rates →
  **UNAVAILABLE** (trusted sold-out). Control empty too → the feed itself is
  broken → **UNKNOWN**, never a silent false "no rooms".
- After 4 consecutive UNKNOWN runs (~1 hour) you get one "monitor may be
  broken" email.
- Once per day at/after 8am ET: a one-line heartbeat so you know it's alive.

A false alarm just costs a tap — the alert links straight to marriott.com to
confirm and book. Caveat: Xotelo is an unofficial free service; if it ever
shuts down or rate-limits, the UNKNOWN alert tells you within the hour.

## Setup

1. **Gmail app password:** Google Account → Security → 2-Step Verification →
   App passwords → create one (requires 2FA on the account).
2. **Create a GitHub repo** and push these files. A **public** repo gets
   unlimited free Actions minutes (secrets stay private); a private repo at
   this cadence bills ~2,900 min/month against a 2,000–3,000 free tier.
3. **Add repository secrets** (Settings → Secrets and variables → Actions):
   | Secret | Required | Value |
   |---|---|---|
   | `GMAIL_ADDRESS` | yes | the Gmail account that sends alerts |
   | `GMAIL_APP_PASSWORD` | yes | from step 1 |
   | `ALERT_EMAIL` | yes | where alerts go |
   | `ALERT_SMS_EMAIL` | no | carrier email-to-SMS address, if yours still works |
   | `NTFY_TOPIC` | no (recommended) | hard-to-guess topic, e.g. `will-hotel-x7k2m9`; install the [ntfy](https://ntfy.sh) app and subscribe to the same topic for instant phone pushes |
4. **Enable Actions** (Actions tab). GitHub often delays cron runs 10–30
   minutes at busy times — worst case an alert lands ~40 min after a room
   opens. Use **Run workflow** to trigger manually anytime.

## Testing

```bash
python3 monitor.py --dry-run      # prints AVAILABLE/UNAVAILABLE/UNKNOWN, sends nothing
```

(If macOS system Python complains about SSL certificates:
`python3 -m venv .venv && .venv/bin/pip install certifi && .venv/bin/python monitor.py --dry-run`)

```bash
export GMAIL_ADDRESS=... GMAIL_APP_PASSWORD=... ALERT_EMAIL=... NTFY_TOPIC=...
python3 monitor.py --force-alert  # sends a [TEST] alert through every configured channel
```

Verified at build time (Aug 24, 2026): target dates correctly report
UNAVAILABLE (feed live via control date), and dates known to have rooms
report `AVAILABLE — Courtyard $233/night`.

## Different hotel or dates

Edit the `CONFIG` dict at the top of `monitor.py`. The `hotel_key` comes from
the hotel's tripadvisor.com URL (`Hotel_Review-g53933-d96877-...` →
`g53933-d96877`); update the booking URL and dates to match.
