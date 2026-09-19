"""
DAILY REPORT  -  who used AR Radar today, mailed out each evening
=================================================================

Run by .github/workflows/daily-report.yml on a schedule. Pulls the day's
numbers from GoatCounter, adds what the board itself currently holds, and
emails a short summary.

WHAT THIS CAN AND CANNOT TELL YOU
---------------------------------
AR Radar is a static page. There are no accounts and no sign-in, so there
is no such thing as "users logged in today" - nobody logs in to anything.
What exists is:

    visitors   - distinct people who opened the link (GoatCounter counts
                 a visitor without cookies, by a rotating daily hash, so
                 the same person on two days counts twice and the figure
                 cannot be tied back to anyone)
    views      - page loads, including someone reopening the tab
    active     - visitors who did something: filtered, sorted, dropped in
                 a resume, opened a job, or clicked through to apply

"Active" is the honest version of "actually used the portal". It comes
from events the page fires, each at most once per visit.

CONFIGURATION (all via environment, nothing in this file)
---------------------------------------------------------
    GOATCOUNTER_CODE   your site code, e.g. "ar-radar"
    GOATCOUNTER_TOKEN  API token from GoatCounter -> Settings -> API
    SMTP_USER          gmail address the report is sent FROM
    SMTP_PASS          a Gmail APP PASSWORD, not the account password
    REPORT_TO          comma-separated recipients

In GitHub these come from repository secrets; the workflow maps them in.
Nothing is ever committed.
"""

import json
import os
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

# India Standard Time. The report is about an Indian audience and an
# Indian job board, so "today" has to mean today in IST - a UTC day
# boundary would cut the evening off and staple it to the next report.
IST = timezone(timedelta(hours=5, minutes=30))

# Events the page fires. Keys are the paths tracked in site_template.html.
EVENTS = {
    "used/filtered": "Filtered the list",
    "used/sorted": "Changed sort order",
    "used/added-resume": "Scored a resume",
    "used/opened-job": "Opened a job",
    "used/clicked-apply": "Clicked through to apply",
}

# The repo is still named ar-desk, from before the site was called AR
# Radar. Renaming it would change this URL and break the link already
# shared, so the name stayed put.
SITE_URL = "https://zlteamworks.github.io/ar-desk/"


def env(name, required=True):
    v = (os.environ.get(name) or "").strip()
    if required and not v:
        sys.exit(f"missing required environment variable: {name}")
    return v


# ------------------------- goatcounter -------------------------

def gc_get(code, token, path, params):
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"https://{code}.goatcounter.com/api/v0/{path}?{qs}"
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        print(f"  ! goatcounter {path} -> HTTP {e.code}: {body}")
        return None
    except Exception as e:
        print(f"  ! goatcounter {path} -> {type(e).__name__}: {e}")
        return None


def collect_traffic(code, token, day):
    """Visits, views and per-event counts for one IST day."""
    start = f"{day}T00:00:00+05:30"
    end = f"{day}T23:59:59+05:30"

    out = {"visitors": 0, "views": 0, "events": {}, "referrers": [], "ok": False}

    total = gc_get(code, token, "stats/total", {"start": start, "end": end})
    if total is None:
        return out
    out["ok"] = True
    out["views"] = total.get("total", 0) or 0
    out["visitors"] = total.get("total_unique", out["views"]) or 0

    hits = gc_get(code, token, "stats/hits", {"start": start, "end": end, "limit": 100})
    for row in ((hits or {}).get("hits") or []):
        path = (row.get("path") or "").lstrip("/")
        if path in EVENTS:
            out["events"][path] = row.get("count_unique") or row.get("count") or 0

    refs = gc_get(code, token, "stats/toprefs", {"start": start, "end": end, "limit": 8})
    for row in ((refs or {}).get("refs") or []):
        name = row.get("name") or "direct / typed the link"
        out["referrers"].append((name, row.get("count_unique") or row.get("count") or 0))

    return out


# ------------------------- the board itself -------------------------

def collect_board():
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "jobs.json"), encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    counts = d.get("counts", {})
    jobs = d.get("jobs", [])
    fresh = sum(1 for j in jobs if (j.get("days_old") is not None
                                    and j["days_old"] <= 1))
    return {
        "ok": True,
        "total": counts.get("total", 0),
        "by_source": counts.get("by_source", {}),
        "families": len(counts.get("by_family", {})),
        "fresh": fresh,
        "generated": d.get("generated_label", "unknown"),
    }


# ------------------------- rendering -------------------------

def render(day, traffic, board):
    active = max(traffic["events"].values()) if traffic["events"] else 0
    pct = round(active / traffic["visitors"] * 100) if traffic["visitors"] else 0

    def row(label, value, note=""):
        return (f'<tr><td style="padding:6px 14px 6px 0;color:#5a6a65">{label}</td>'
                f'<td style="padding:6px 0;font-weight:bold;text-align:right">{value}</td>'
                f'<td style="padding:6px 0 6px 14px;color:#8a9a95;font-size:13px">{note}</td></tr>')

    if not traffic["ok"]:
        audience = ('<p style="color:#a33a2e">Could not read GoatCounter today — '
                    'the numbers below are missing, not zero. Check the API token.</p>')
    elif traffic["visitors"] == 0:
        audience = '<p style="color:#6b7a75">Nobody opened the link today.</p>'
    else:
        audience = "<table>" + "".join([
            row("Visitors", traffic["visitors"], "distinct people who opened the link"),
            row("Page views", traffic["views"], "including reopened tabs"),
            row("Actively used it", active, f"{pct}% of visitors did something"),
        ]) + "</table>"

    ev = "".join(
        f'<tr><td style="padding:4px 14px 4px 0;color:#5a6a65">{EVENTS[k]}</td>'
        f'<td style="padding:4px 0;text-align:right;font-weight:bold">{v}</td></tr>'
        for k, v in sorted(traffic["events"].items(), key=lambda kv: -kv[1]) if v
    )
    ev_html = f"<table>{ev}</table>" if ev else \
        '<p style="color:#8a9a95">No one interacted with the page today.</p>'

    refs = "".join(
        f'<tr><td style="padding:4px 14px 4px 0;color:#5a6a65">{n}</td>'
        f'<td style="padding:4px 0;text-align:right;font-weight:bold">{c}</td></tr>'
        for n, c in traffic["referrers"]
    )
    refs_html = f"<table>{refs}</table>" if refs else \
        '<p style="color:#8a9a95">No referrer data.</p>'

    if board["ok"]:
        src = " &nbsp;·&nbsp; ".join(f"{k} {v}" for k, v in
                                     sorted(board["by_source"].items(), key=lambda kv: -kv[1]))
        board_html = "<table>" + "".join([
            row("Roles on the board", board["total"], src),
            row("Posted in last 24h", board["fresh"]),
            row("Job families", board["families"]),
            row("Last refresh", board["generated"]),
        ]) + "</table>"
    else:
        board_html = f'<p style="color:#a33a2e">Could not read jobs.json: {board.get("error")}</p>'

    return f"""<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#10201c;max-width:640px">
  <h2 style="margin:0 0 4px">AR Radar — {day}</h2>
  <p style="margin:0 0 22px;color:#6b7a75;font-size:13px">
    <a href="{SITE_URL}" style="color:#0e6e5c">{SITE_URL}</a>
  </p>

  <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:#6b7a75;margin:0 0 8px">Audience</h3>
  {audience}

  <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:#6b7a75;margin:26px 0 8px">What they did</h3>
  {ev_html}

  <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:#6b7a75;margin:26px 0 8px">Where they came from</h3>
  {refs_html}

  <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:#6b7a75;margin:26px 0 8px">The board</h3>
  {board_html}

  <p style="margin:30px 0 0;color:#8a9a95;font-size:12px;line-height:1.6">
    Counted without cookies and without anything that identifies a person.
    There is no sign-in on AR Radar, so these are visitors, not accounts.
  </p>
</div>"""


def send(html, day, to_addrs, user, password):
    msg = EmailMessage()
    msg["Subject"] = f"AR Radar — daily report, {day}"
    msg["From"] = user
    msg["To"] = ", ".join(to_addrs)
    msg.set_content("This report is formatted in HTML. "
                    f"See {SITE_URL} for the live board.")
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465,
                          context=ssl.create_default_context()) as s:
        s.login(user, password)
        s.send_message(msg)


def main():
    day = os.environ.get("REPORT_DATE") or datetime.now(IST).date().isoformat()

    code = env("GOATCOUNTER_CODE")
    token = env("GOATCOUNTER_TOKEN")
    user = env("SMTP_USER")
    password = env("SMTP_PASS")
    to_addrs = [a.strip() for a in env("REPORT_TO").split(",") if a.strip()]

    print(f"building report for {day} (IST)")
    traffic = collect_traffic(code, token, day)
    board = collect_board()
    print(f"  visitors={traffic['visitors']} views={traffic['views']} "
          f"events={traffic['events']}")

    html = render(day, traffic, board)
    send(html, day, to_addrs, user, password)
    print(f"  sent to {', '.join(to_addrs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
