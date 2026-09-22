"""
SITE DATA EXPORT  -  turns a bot run into the website's data file
=================================================================

The public Job Bot page is a single self-contained web page: it cannot
call Naukri, LinkedIn or Workday itself (browsers block cross-site
requests, and the portals would block the traffic anyway). So the data
has to travel with the page. This module writes that payload.

Run flow:
    python naukri_job_bot.py      ->  jobs.xlsx  +  jobs.json
    claude ... (republish)        ->  the live site picks up jobs.json

WHAT GOES IN, AND WHAT DELIBERATELY DOES NOT
--------------------------------------------
The page scores each visitor's own resume against each job IN THEIR
BROWSER, so it needs real job text to match against. That text is
included for Naukri and Workday rows.

Every scored row is exported - there is no shortlist cut any more. The
page's filters (family, city, salary, experience, freshness) all run
against fields shipped here, which is why this module normalises cities
and parses salary and experience into numbers: a browser cannot filter a
slider against the string "Est. (from experience)".

LinkedIn rows carry title, company, location and a link OUT to the
original posting - and nothing else. Republishing scraped LinkedIn job
descriptions on a public site is the thing that actually draws a
complaint; linking to them is what every job aggregator already does.
strip_for_publication() enforces that, so it cannot be forgotten.

Recruiter emails and phone numbers are published only through the dedicated
contact field when they were explicitly included in the source job posting.
They remain redacted from titles, company names and description text so a
contact is never exposed accidentally or without the UI's source label.
"""

import json
import os
import re
from datetime import datetime, timedelta

# Sources whose job text may be republished on the public page.
REPUBLISHABLE_TEXT = {"Naukri", "Workday"}

# How much JD text to ship per job. The visitor's browser matches against
# this, so it needs to be substantial - but 61 jobs x unlimited text would
# bloat the page. 1,800 chars covers responsibilities and requirements,
# which is where the matchable keywords live.
JD_CHARS = 1800


def _clean(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


# Portals write the same city a dozen ways - "Bangalore", "Bengaluru",
# "Hyderabad(Gachibowli)", "Mumbai (All Areas)", "Gurgaon/Gurugram" - and
# a filter chip built from those raw strings would show six chips for one
# city and match none of them reliably. Normalise once, here.
CITY_CANON = {
    "bengaluru": "Bengaluru", "bangalore": "Bengaluru", "banglore": "Bengaluru",
    "chennai": "Chennai", "madras": "Chennai",
    "hyderabad": "Hyderabad", "secunderabad": "Hyderabad",
    "mumbai": "Mumbai", "bombay": "Mumbai", "navi mumbai": "Mumbai",
    "thane": "Mumbai",
    "pune": "Pune",
    "gurgaon": "Gurgaon", "gurugram": "Gurgaon",
    "noida": "Noida",
    "delhi": "Delhi", "ncr": "Delhi",
    "kolkata": "Kolkata", "calcutta": "Kolkata",
    "coimbatore": "Coimbatore",
    "ahmedabad": "Ahmedabad", "jaipur": "Jaipur", "kochi": "Kochi",
    "chandigarh": "Chandigarh", "indore": "Indore",
}


def _cities_in(location):
    """Every canonical city named in a location string. A job listed in
    'Bengaluru, Chennai' genuinely belongs under both chips."""
    low = (location or "").lower()
    if not low.strip():
        return []
    if "remote" in low:
        return ["Remote"]
    found = []
    for key, canon in CITY_CANON.items():
        if key in low and canon not in found:
            found.append(canon)
    return found or ["Other"]


# Recruiters leave direct email addresses and mobile numbers in job
# descriptions. Those belong to named individuals who posted a req on a
# portal - they did not agree to appear on a public page, and a public page
# is exactly where a scraper harvests them from. jobs.xlsx keeps the full
# details for the owner's own outreach; nothing published carries them.
#
# This has to run over EVERY published string, not just the "contact"
# field. The field is only where the bot files a contact it recognised;
# the same email and phone usually also sit in the raw JD body, and a
# handful even end up in the company name. Redacting one field and
# shipping the JD republishes the data anyway.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Indian mobile numbers: 10 digits opening 6-9, with an optional +91. The
# lookarounds keep it off salary figures and long ID strings. Written to
# match the pattern the bot itself extracts in extract_contact().
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)")


def redact_contacts(text):
    """Strip direct emails and phone numbers out of publishable text."""
    if not text:
        return text
    text = _EMAIL_RE.sub("[email removed]", str(text))
    text = _PHONE_RE.sub("[phone removed]", text)
    return text


def publish_contact(text):
    """Return only email/Indian-mobile tokens explicitly found in a posting.

    The extractor already stores this compact value on the private job row.
    Re-validating it here prevents surrounding JD text or arbitrary markup
    from leaking into the public contact field.
    """
    if not text or str(text).strip() in ("", "-"):
        return "Not Available"
    raw = str(text)
    emails = list(dict.fromkeys(_EMAIL_RE.findall(raw)))
    phones = list(dict.fromkeys(_PHONE_RE.findall(raw)))
    return " | ".join((emails + phones)[:2]) or "Not Available"


def strip_for_publication(job):
    """
    Decide what text this row is allowed to publish. Returns the JD text
    to embed, which is empty for sources we only link to.
    """
    if job.get("source") not in REPUBLISHABLE_TEXT:
        return ""
    return redact_contacts(_clean(job.get("description", ""))[:JD_CHARS])


def _agency(company):
    """
    Is this posting coming through an intermediary rather than the employer?

    The bot decides this with is_consultancy() at spreadsheet-write time and
    never stores it on the job dict, so reading a flag off the row silently
    returns False for everything - which renders every agency repost as a
    direct employer req. Ask the real function instead.

    The lazy import matters: naukri_job_bot imports THIS module from inside
    main(), so a module-level import here would be circular.
    """
    if not company:
        return False
    try:
        import naukri_job_bot
        return naukri_job_bot.is_consultancy(company)
    except Exception:
        return False


def _posted_via(job):
    """
    "direct" | "agency" | "unverified" - who you actually reach by applying.

    This is deliberately NOT a yes/no. Name-matching catches "Venpa Staffing"
    and "Manpowergroup", but never "Rrayze Business Solutions" or "Taas
    Partners", and widening the patterns would smear real employers instead
    ("Business Solutions" and "Partners" both appear in genuine company
    names). A binary therefore has to guess on the unmatched majority, and
    guessing "direct" is the expensive mistake: directness is the heaviest
    term in the odds model, so a wrong green badge sends someone to spend
    real effort on an agency repost.

    So green is claimed only where there is proof. A Workday row comes from
    the employer's OWN applicant tracking system - there is no intermediary
    by construction. Everything else is either a matched agency, or honestly
    labelled unverified.
    """
    if job.get("source") == "Workday" or job.get("_direct_employer"):
        return "direct"
    if _agency(job.get("company", "")):
        return "agency"
    return "unverified"


def _numeric_range(text, cap=99.0):
    """'10-15' -> (10, 15); '10+' -> (10, cap); '5 Yrs' -> (5, 5).
    The page needs numbers to run a slider against - it cannot filter on
    'Est. (from experience)'."""
    nums = re.findall(r"\d+(?:\.\d+)?", str(text or ""))
    if not nums:
        return None, None
    lo = float(nums[0])
    if len(nums) > 1:
        return lo, float(nums[1])
    return lo, (cap if "+" in str(text) else lo)


def _publish_company(company):
    """
    Company name, with contacts redacted.

    A few postings put the recruiter's email in the company field and
    nothing else. Redacting those leaves a row whose employer reads
    "[email removed]", so fall back to the page's existing unknown-company
    label instead of showing the redaction as a name.
    """
    name = redact_contacts(_clean(company))
    if name:
        name = re.sub(r"\[(?:email|phone) removed\]", "", name).strip(" -,/|")
    return name or "(not named)"


def _row(job, tier, index):
    jd = strip_for_publication(job)
    # Preserve requirement clauses beyond the display excerpt. Link-only
    # sources retain no description or derived requirement text.
    full_text = redact_contacts(_clean(job.get("description", ""))) if jd else ""
    requirement_parts = re.split(r"(?<=[.;!?])\s+", full_text)
    requirement_text = "\n".join(part for part in requirement_parts if re.search(
        r"\b(required|mandatory|must|essential|preferred|desirable|qualification|experience|notice|shift|relocat|certif|degree)\w*\b", part, re.I))[:7000]
    n = job.get("applicants")
    sal_lo, sal_hi = _numeric_range(job.get("salary_band"))
    exp_lo, exp_hi = _numeric_range(job.get("experience"), cap=40.0)
    return {
        "id": job.get("job_id") or f"{tier}-{index}",
        "tier": tier,
        "score": job.get("score", 0),
        "verdict": job.get("verdict") or "",
        "source": job.get("source", ""),
        "family": job.get("family") or "Finance",
        "title": redact_contacts(_clean(job.get("title"))),
        "company": _publish_company(job.get("company")),
        "posted_via": _posted_via(job),
        "posted": job.get("posted_date", "?"),
        "age": job.get("age", "?"),
        "days_old": job.get("days_old"),
        "location": _clean(job.get("location")),
        "cities": _cities_in(job.get("location")),
        "experience": _clean(job.get("experience")) or "",
        "salary_band": job.get("salary_band", "-"),
        "salary_basis": job.get("salary_basis", ""),
        "sal_lo": sal_lo,
        "sal_hi": sal_hi,
        "exp_lo": exp_lo,
        "exp_hi": exp_hi,
        "applicants": None if n is None else int(n),
        "type": _clean(job.get("employment_type")) or "",
        "apply_method": _clean(job.get("apply_method")) or "",
        "ar": job.get("ar", 0),
        "why": redact_contacts(_clean(job.get("why"))),
        # Everything the posting asks for, not a diff against anyone's CV.
        # The page subtracts the visitor's own uploaded resume from this,
        # in their browser - so there is no owner_resume_match to publish.
        "missing": redact_contacts(_clean(job.get("missing"))),
        # This is intentionally separate from the redacted JD. The UI labels
        # it as source-posted contact information and never guesses a person.
        "contact": publish_contact(job.get("contact")),
        "url": job.get("url", ""),
        "skills": redact_contacts(_clean(job.get("skills"))[:400]),
        # Empty for link-only sources - see strip_for_publication().
        "jd": jd,
        "jd_truncated": len(full_text) > len(jd),
        "requirement_text": requirement_text,
        "has_text": bool(jd),
    }


def build_payload(shortlist, near_misses, stats, config):
    now = datetime.now()
    jobs = []
    for i, job in enumerate(shortlist):
        jobs.append(_row(job, "shortlist", i))
    for i, job in enumerate(near_misses):
        jobs.append(_row(job, "near", i))

    by_source = {}
    by_family = {}
    by_city = {}
    for j in jobs:
        by_source[j["source"]] = by_source.get(j["source"], 0) + 1
        by_family[j["family"]] = by_family.get(j["family"], 0) + 1
        for city in _cities_in(j["location"]):
            by_city[city] = by_city.get(city, 0) + 1

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_label": now.strftime("%d %b %Y, %H:%M"),
        "config": config,
        "stats": [[str(label), value] for label, value in stats],
        "counts": {
            "total": len(jobs),
            "shortlist": sum(1 for j in jobs if j["tier"] == "shortlist"),
            "near": sum(1 for j in jobs if j["tier"] == "near"),
            "by_source": by_source,
            # The page builds its filter chips from these, so a family or
            # city with no jobs this run simply does not get a chip -
            # better than offering a filter that returns nothing.
            "by_family": by_family,
            "by_city": by_city,
            "with_text": sum(1 for j in jobs if j["has_text"]),
        },
        "jobs": jobs,
    }


def _refresh_counts(payload):
    jobs = payload.get("jobs", [])
    by_source, by_family, by_city = {}, {}, {}
    for job in jobs:
        source, family = job.get("source", ""), job.get("family") or "Finance"
        by_source[source] = by_source.get(source, 0) + 1
        by_family[family] = by_family.get(family, 0) + 1
        for city in (job.get("cities") or _cities_in(job.get("location"))):
            by_city[city] = by_city.get(city, 0) + 1
    payload["counts"] = {
        "total": len(jobs),
        "shortlist": sum(1 for job in jobs if job.get("tier") == "shortlist"),
        "near": sum(1 for job in jobs if job.get("tier") == "near"),
        "by_source": by_source, "by_family": by_family, "by_city": by_city,
        "with_text": sum(1 for job in jobs if job.get("has_text")),
    }


def merge_previous_payload(payload, previous, max_days_old, now=None):
    """Merge a parsed prior payload into a newly collected payload."""
    now = now or datetime.now()
    try:
        previous_at = datetime.fromisoformat(previous.get("generated_at", ""))
    except (ValueError, TypeError):
        return 0
    elapsed_days = max(0, (now.date() - previous_at.date()).days)
    current_ids = {str(job.get("id")) for job in payload.get("jobs", [])}
    def listing_key(job):
        return tuple(re.sub(r"[^a-z0-9]", "", str(job.get(field) or "").lower())[:limit]
                     for field, limit in (("title", 60), ("company", 40), ("location", 20)))
    current_keys = {listing_key(job) for job in payload.get("jobs", [])}
    retained = []
    for old in previous.get("jobs", []):
        old_id = str(old.get("id") or "")
        old_key = listing_key(old)
        prior_age = old.get("days_old")
        if (not old_id or old_id in current_ids or old_key in current_keys
                or not isinstance(prior_age, int)):
            continue
        age = prior_age + elapsed_days
        if age > max_days_old or not old.get("url"):
            continue
        row = dict(old)
        row["days_old"] = age
        row["age"] = "Today" if age <= 0 else "Yesterday" if age == 1 else f"{age}d ago"
        row["posted"] = (now - timedelta(days=age)).strftime("%d-%b-%Y")
        row["carried_forward"] = True
        if row.get("source") == "Workday" and not row.get("apply_method"):
            row["apply_method"] = "Employer site"
        retained.append(row)
        current_ids.add(old_id)
        current_keys.add(old_key)
    if retained:
        payload["jobs"].extend(retained)
        payload.setdefault("stats", []).append(["Retained from rolling 7-day snapshot", len(retained)])
        _refresh_counts(payload)
    return len(retained)


def merge_recent_snapshot(payload, path, max_days_old):
    """Keep still-fresh roles missed by a throttled daily portal sweep.

    Portal result sets are sampled and non-deterministic: a live seven-day
    role can disappear from today's first pages even though it was collected
    yesterday.  The public payload is therefore also a bounded rolling cache.
    Rows age out normally and a freshly collected row always wins by id.
    """
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as handle:
            previous = json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0
    return merge_previous_payload(payload, previous, max_days_old)


def export_site_json(shortlist, near_misses, stats, config, path):
    """Write the website payload. Returns the path written."""
    payload = build_payload(shortlist, near_misses, stats, config)
    retained = merge_recent_snapshot(payload, path, int(config.get("max_days_old", 7)))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    size = os.path.getsize(path)
    print(f"  -> {os.path.basename(path)} "
          f"({payload['counts']['total']} jobs, "
          f"{payload['counts']['with_text']} with matchable text, "
          f"{size // 1024} KB"
          + (f", {retained} retained from prior run" if retained else "") + ")")
    return path
