"""
WORKDAY SOURCE  -  direct-employer job feed for the AR shortlist bot
====================================================================

WHY WORKDAY IS WORTH THE TROUBLE
--------------------------------
Naukri and LinkedIn are job BOARDS: agencies repost the same req under
five names and the real employer is often hidden. Workday is the
employer's OWN applicant tracking system, so every row here is by
definition a direct req - the single strongest signal in the interview-
odds model (W_DIRECTNESS). No agencies, no "confidential client".

HOW IT WORKS
------------
Every Workday career site exposes the same JSON endpoint its own front
end calls:

    POST https://<tenant>.<host>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs
    {"appliedFacets":{}, "limit":20, "offset":0, "searchText":"accounts receivable"}

No auth, no anti-bot token, no browser needed - plain stdlib urllib is
enough, which is why this module has zero new dependencies and does not
touch Playwright at all.

THE CATCH: THERE IS NO GLOBAL WORKDAY SEARCH
--------------------------------------------
You search one employer at a time, and the three URL parts cannot be
guessed from a company name:
  * tenant - usually the company slug, but Nike's is "nke"
  * host   - the datacenter (wd1/wd3/wd5/wd10/wd12), not derivable at all
  * site   - the career-site name: "Cisco_Careers", "jobs", "External"...
Guessing scored 1-in-12 in testing. So TENANTS below is a curated list of
verified triples, and adding an employer means reading the three parts off
their real careers URL:

    https://cisco.wd5.myworkdayjobs.com/Cisco_Careers
            ^tenant ^host              ^site

Add one, re-run, done. That is the only maintenance this module needs.
"""

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# ============================ CONFIG ============================

# Verified tenant triples: (display name, tenant, host, site).
# Every one of these was confirmed live against the API - see the module
# docstring for how to add your own.
TENANTS = [
    ("Cisco",           "cisco",            "wd5", "Cisco_Careers"),
    ("Micron",          "micron",           "wd1", "External"),
    ("Motorola Solutions", "motorolasolutions", "wd5", "Careers"),
    ("S&P Global",      "spgi",             "wd5", "SPGI_Careers"),
    ("Intel",           "intel",            "wd1", "External"),
    ("Zoom",            "zoom",             "wd5", "zoom"),
    ("CrowdStrike",     "crowdstrike",      "wd5", "CROWDSTRIKECareers"),
    ("Zendesk",         "zendesk",          "wd1", "zendesk"),
    ("Boeing",          "boeing",           "wd1", "External_Careers"),
    ("3M",              "3m",               "wd1", "Search"),
    ("Pfizer",          "pfizer",           "wd1", "PFIZERCareers"),
    ("Amgen",           "amgen",            "wd1", "Careers"),
    ("Gilead Sciences", "gilead",           "wd1", "GileadCareers"),
    ("Regeneron",       "regeneron",        "wd1", "Careers"),
    ("Cigna",           "cigna",            "wd5", "CIGNACareers"),
    ("Humana",          "humana",           "wd5", "Humana_External_Career_Site"),
    ("PNC",             "pnc",              "wd5", "External"),
    ("Truist",          "truist",           "wd1", "Careers"),
    ("T. Rowe Price",   "troweprice",       "wd5", "TROWEPRICE"),
    ("Prudential",      "prudential",       "wd3", "Prudential"),
    ("AIG",             "aig",              "wd1", "aig"),
    ("Allstate",        "allstate",         "wd5", "ALLSTATE_Careers"),
    ("Travelers",       "travelers",        "wd5", "External"),
    ("Chevron",         "chevron",          "wd5", "jobs"),
    ("ConocoPhillips",  "conocophillips",   "wd1", "External"),
    ("Baker Hughes",    "bakerhughes",      "wd5", "bakerhughes"),
    ("Target",          "target",           "wd5", "TargetCareers"),
    ("Gap Inc",         "gapinc",           "wd1", "gapinc"),
    ("Chipotle",        "chipotle",         "wd5", "ChipotleCareers"),
    ("NVIDIA",          "nvidia",           "wd5", "NVIDIAExternalCareerSite"),
    ("Adobe",           "adobe",            "wd5", "external_experienced"),
    ("Salesforce",      "salesforce",       "wd12", "External_Career_Site"),
    ("Workday",         "workday",          "wd5", "Workday"),

    # --- India-yield tenants -------------------------------------------
    # The block above is a US-employer list: it proves the API works but
    # returned ~20 India rows across 29 firms, almost none of them AR.
    # These were found by reading real job URLs off search results, and
    # they are the ones that actually carry AR/O2C reqs in your cities.
    ("Genpact",         "genpact",          "wd108", "External_Careers"),
    ("CPSI",            "cpsi",             "wd1", "CPSI"),
    ("Flex",            "flextronics",      "wd1", "Careers"),
    ("Autodesk",        "autodesk",         "wd1", "Ext"),
    ("Trimble",         "trimble",          "wd1", "TrimbleCareers"),
    ("Finastra",        "finastra",         "wd3", "FINC"),
    # Valid slug, and note it is lowercase - "ExternalCareerSite" answers
    # with zero rows, which looks identical to a healthy but quiet tenant.
    ("HP",              "hp",               "wd5", "externalcareersite"),
]

# Workday's own relevance search is good, so a handful of broad stems beats
# a long list of near-duplicate titles - same reasoning as the Naukri queries.
WORKDAY_QUERIES = [
    "accounts receivable",
    "order to cash",
    "collections",
    "credit control",
    "billing analyst",
]

RESULTS_PER_PAGE = 20      # Workday's default page size
MAX_PAGES = 2              # 40 rows per query per employer is plenty
CONCURRENCY = 8            # these are small JSON calls to many hosts
TIMEOUT_S = 20
DETAIL_CONCURRENCY = 6
DETAIL_LIMIT = 80          # cap full-JD fetches so a run can't blow out

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")

# Workday returns worldwide results; we keep only rows whose location text
# reads Indian. Matching on tokens (not exact city equality) because Workday
# writes things like "Bengaluru, Karnataka, India" and "India - Remote".
INDIA_TOKENS = (
    "india", "bengaluru", "bangalore", "chennai", "hyderabad", "pune",
    "mumbai", "gurgaon", "gurugram", "noida", "kolkata", "coimbatore",
    "delhi", "ahmedabad",
)

_CTX = ssl.create_default_context()


# ------------------------- transport -------------------------

# Workday takes its datacenters down for scheduled maintenance, typically
# Friday evening into Saturday morning US Pacific - which is Saturday
# morning IST, exactly when the 7am refresh runs. During the window the
# endpoint answers 200 with an HTML "Workday is currently unavailable"
# page instead of JSON.
#
# This matters because the failure is indistinguishable from a dead
# tenant unless you look: both produce no rows. A run inside the window
# once looked like 38 employers had silently changed their career-site
# slugs, when nothing was wrong at all. MAINTENANCE is the sentinel that
# tells the two apart.
MAINTENANCE = object()

_MAINT_MARKERS = ("currently unavailable", "maintenance-page",
                  "service interruption")


def _looks_like_maintenance(raw):
    low = raw[:4000].lower()
    return any(m in low for m in _MAINT_MARKERS)


def _post_json(url, payload):
    """One POST -> parsed JSON, MAINTENANCE, or None. Never raises: a dead
    tenant must not take the whole run down with it."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_CTX) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return MAINTENANCE if _looks_like_maintenance(raw) else None


def _get_json(url):
    req = urllib.request.Request(
        url, headers={"accept": "application/json", "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_CTX) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def api_base(tenant, host, site):
    return (f"https://{tenant}.{host}.myworkdayjobs.com"
            f"/wday/cxs/{tenant}/{site}")


def public_url(tenant, host, site, external_path):
    return (f"https://{tenant}.{host}.myworkdayjobs.com/en-US/{site}"
            f"{external_path}")


# ------------------------- parsing -------------------------

def parse_posted_on(text):
    """
    'Posted Today' | 'Posted Yesterday' | 'Posted 9 Days Ago' |
    'Posted 30+ Days Ago' -> days as an int, or None.
    """
    if not text:
        return None
    s = text.lower()
    if "today" in s or "just posted" in s:
        return 0
    if "yesterday" in s:
        return 1
    m = re.search(r"(\d+)\+?\s*day", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\+?\s*month", s)
    if m:
        return int(m.group(1)) * 30
    return None


def looks_indian(location_text):
    low = (location_text or "").lower()
    return any(tok in low for tok in INDIA_TOKENS)


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&#39;", "'").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", text).strip()


def normalize(posting, company, tenant, host, site):
    """Map a Workday jobPosting onto the same dict shape the bot's Naukri
    and LinkedIn rows use, so scoring and Excel output need no changes."""
    path = posting.get("externalPath", "") or ""
    title = (posting.get("title") or "").strip()
    if not title:
        return None
    loc = (posting.get("locationsText") or "").strip()
    req_id = ""
    bullets = posting.get("bulletFields") or []
    if bullets:
        req_id = str(bullets[0])
    return {
        "source": "Workday",
        "job_id": "W:" + (req_id or path or title),
        "workday_path": path,
        "workday_ref": (tenant, host, site),
        "title": title,
        # Workday IS the employer's own system, so the company is known and
        # never an agency - this is what earns the directness score.
        "company": company,
        "employment_type": "",
        "experience": "",
        "salary": "",
        "location": loc,
        "skills": "",
        "description": "",
        "days_old": parse_posted_on(posting.get("postedOn", "")),
        "reviews": "",
        "applicants": None,
        "url": public_url(tenant, host, site, path),
        "_portal_ctc": False,
        "_direct_employer": True,
    }


# ------------------------- fetching -------------------------

def _search_one(args):
    company, tenant, host, site, query, page = args
    url = api_base(tenant, host, site) + "/jobs"
    payload = {
        "appliedFacets": {},
        "limit": RESULTS_PER_PAGE,
        "offset": page * RESULTS_PER_PAGE,
        "searchText": query,
    }
    data = _post_json(url, payload)
    if data is MAINTENANCE or not isinstance(data, dict):
        return []
    rows = []
    for posting in (data.get("jobPostings") or []):
        if not looks_indian(posting.get("locationsText", "")):
            continue
        job = normalize(posting, company, tenant, host, site)
        if job:
            rows.append(job)
    return rows


def fetch_workday(queries=None, max_days_old=None, verbose=True):
    """
    Sweep every tenant x query in parallel and return normalized job dicts,
    deduped, India-only, freshness-filtered.
    """
    queries = queries or WORKDAY_QUERIES
    tasks = [(name, tenant, host, site, q, pg)
             for (name, tenant, host, site) in TENANTS
             for q in queries
             for pg in range(MAX_PAGES)]

    if verbose:
        print(f"[Workday]  {len(TENANTS)} employers x {len(queries)} queries "
              f"= {len(tasks)} calls ({CONCURRENCY} at a time)...")

    t0 = time.time()
    jobs, seen = [], set()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for rows in pool.map(_search_one, tasks):
            for job in rows:
                if job["job_id"] in seen:
                    continue
                if (max_days_old is not None
                        and job["days_old"] is not None
                        and job["days_old"] > max_days_old):
                    continue
                seen.add(job["job_id"])
                jobs.append(job)

    if verbose:
        firms = len({j["company"] for j in jobs})
        print(f"[Workday]  {len(jobs)} India rows from {firms} employers "
              f"in {time.time() - t0:.0f}s")
    return jobs


# ------------------------- enrichment -------------------------

def _detail_one(job):
    """Full JD for one row. Workday exposes it at the same cxs path with
    the posting's externalPath appended."""
    tenant, host, site = job["workday_ref"]
    url = api_base(tenant, host, site) + job["workday_path"]
    data = _get_json(url)
    if not isinstance(data, dict):
        return job
    info = data.get("jobPostingInfo") or {}
    desc = strip_html(info.get("jobDescription", ""))
    if desc:
        job["description"] = desc
    job["employment_type"] = (info.get("timeType") or
                              info.get("jobRequisitionLocation", {}).get("type", "") or
                              job["employment_type"])
    if info.get("startDate"):
        job["_start_date"] = info["startDate"]
    # Workday almost never publishes CTC for Indian reqs, but when a
    # country requires pay transparency it lands here.
    for key in ("payRangeMinimum", "payRangeMaximum", "compensation"):
        if info.get(key):
            job["salary"] = str(info[key])
            break
    job["_full_jd"] = True
    return job


def enrich_workday(jobs, limit=DETAIL_LIMIT, verbose=True):
    targets = [j for j in jobs if j.get("source") == "Workday"][:limit]
    if not targets:
        return
    if verbose:
        print(f"[Workday]  full JD for {len(targets)} rows...")
    with ThreadPoolExecutor(max_workers=DETAIL_CONCURRENCY) as pool:
        list(pool.map(_detail_one, targets))


# ------------------------- tenant health -------------------------

def verify_tenants(verbose=True):
    """
    Ping every configured tenant once. Career sites do get renamed, and a
    silently dead tenant just shrinks the feed without telling you - run
    this whenever the Workday row count drops.
    """
    def check(entry):
        name, tenant, host, site = entry
        data = _post_json(api_base(tenant, host, site) + "/jobs",
                          {"appliedFacets": {}, "limit": 1, "offset": 0,
                           "searchText": "accounts receivable"})
        if data is MAINTENANCE:
            return name, "maint", 0
        ok = isinstance(data, dict) and "jobPostings" in data
        return name, "ok" if ok else "dead", (data.get("total", 0) if ok else 0)

    results = []
    label = {"ok": "ok  ", "maint": "MAINT", "dead": "DEAD"}
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for name, status, total in pool.map(check, TENANTS):
            results.append((name, status, total))
            if verbose:
                print(f"  {label[status]:<5} {name:<22} {total}")

    maint = [n for n, s, _ in results if s == "maint"]
    dead = [n for n, s, _ in results if s == "dead"]
    if verbose and maint:
        # Say this loudly. The obvious reading of a wall of empty results
        # is "my config broke", and that sends you re-reading 38 career
        # URLs to fix something that fixes itself in a few hours.
        print(f"\n  {len(maint)} tenant(s) are in a Workday maintenance "
              f"window - NOT misconfigured. Nothing to fix; they come back "
              f"on their own. Re-run later to see the real numbers.")
    if verbose and dead:
        print(f"\n  {len(dead)} tenant(s) need their slug re-read from the "
              f"careers URL: {', '.join(dead)}")
    return results


if __name__ == "__main__":
    print("Verifying configured Workday tenants...\n")
    verify_tenants()
    print("\nSearching for India AR roles...\n")
    rows = fetch_workday()
    for j in sorted(rows, key=lambda r: (r["days_old"] is None, r["days_old"] or 99))[:25]:
        age = "?" if j["days_old"] is None else f"{j['days_old']}d"
        print(f"  {age:>4}  {j['company']:<18} {j['title'][:52]:<52} {j['location'][:28]}")
