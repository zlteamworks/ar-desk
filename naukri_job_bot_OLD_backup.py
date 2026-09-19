"""
JOB FINDER (real-browser version)  -  Naukri + LinkedIn
Setup once:   pip install playwright openpyxl pdfplumber python-docx
              playwright install
Then run:     python naukri_job_bot.py

Your resume and the output Excel are read from / written to THIS script's
own folder, so it works no matter where the terminal is pointing.

WHAT THIS VERSION DOES
----------------------
This build is tuned end-to-end for ACCOUNTS RECEIVABLE (AR / Order-to-Cash)
roles - cash application, collections, credit control, billing, disputes &
deductions, AR ageing / DSO.

0. "GIVE ME EVERYTHING" MASTER SWITCH. Set GET_EVERYTHING = True (default is
   False) to keep EVERY posting both portals surface for your titles/cities -
   recruiter and consultancy reposts included. Nothing is dropped for weak AR
   signal, weak resume overlap, or experience level. The only two limits left
   are your cities and the date window (POST_WINDOW_DAYS). The best-fit jobs
   are still sorted to the top - nothing is thrown away, only ordered.
   New "Posted By" column labels each row Direct vs Recruiter so recruiter
   posts are visible instead of buried.

   (Set GET_EVERYTHING = False for the strict AR-only behaviour.)

1. HIDDEN AR OPPORTUNITIES. Lots of real AR jobs are never titled
   "Accounts Receivable" - they hide as "Accounts Executive", "Finance
   Executive", "Process Associate - Finance & Accounting", "O2C Analyst",
   "Credit Analyst", etc. This version:
     - also SEARCHES those adjacent titles (INCLUDE_HIDDEN_SEARCHES),
     - scores every job for AR content (AR % column), and
     - flags the ones whose content is clearly AR but whose title
       doesn't say so (Hidden? = Yes).
2. LESS-CROWDED, FRESH JOBS FIRST. "Applicants" and "Low-comp %" signals.
   The ranking rewards jobs with few/no applicants that were posted recently.
3. REPORTING COLUMNS:
     Portal | Role | Company | Posted By | Type (FT/PT) | Location |
     Experience | Salary | Applicants | Posting Date | Resume Match % |
     Missing Skills in Resume | Portal Pref (est.) | Job Link |
     AR % | Hidden? | Score
4. Everything from the previous version (strict location, real resume
   coverage %, headless/locked running) is kept.
"""

import os
import re
import html
import time
import math
from collections import Counter
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


# ===================== CONFIG (edit this box) =====================

# ===================== MASTER MODE (read me first) =====================
# GET_EVERYTHING = True  ->  "give me ALL the jobs".
#   Every posting both portals surface for the titles/cities below is kept,
#   INCLUDING recruiter / consultancy reposts. Nothing is dropped for low
#   AR signal, weak resume overlap, or experience level. Only two limits
#   remain: your cities (LOCATION_STRICT) and the date window (POST_WINDOW_DAYS
#   below). Best-fit jobs are still sorted to the top - nothing is discarded.
#
# GET_EVERYTHING = False ->  the strict AR-only behaviour.
#
# Reality check: "all jobs" means all that Naukri/LinkedIn show to a logged-
# out scraper within PAGES_PER_ROLE pages - not literally every job on earth.
# Wider net + more pages = slower runs and more CAPTCHAs. Keep SHOW_BROWSER
# True so you can clear them.
GET_EVERYTHING = False

# Include postings up to this many days old (applies in both modes). Portals
# only expose a limited history to guest search anyway.
POST_WINDOW_DAYS = 2
# =======================================================================

TARGET_ROLES = [
    # Accounts Receivable (titled)
    "Accounts Receivable Analyst",
    "Senior Accounts Receivable Analyst",
    "AR Analyst",
    "Accounts Receivable Specialist",
    "Accounts Receivable Executive",
    "Accounts Receivable Team Lead",
    "Order to Cash Analyst",
    "O2C Analyst",
    "Collections Analyst",
    "Credit Control Analyst",
    "Cash Application Analyst",
    "Billing Analyst",
]

# Titles that are usually AR work but rarely SAY "Accounts Receivable" -
# these are where the hidden opportunities live. Searched in addition to
# TARGET_ROLES when INCLUDE_HIDDEN_SEARCHES is on. (Trim this list if runs
# take too long.)
HIDDEN_AR_TITLES = [
    "Accounts Executive",
    "Finance Executive",
    "Accountant",
    "Finance Associate",
    "Process Associate - Finance and Accounting",
    "Senior Process Associate - O2C",
    "Invoice to Cash Analyst",
    "Revenue Analyst",
    "Billing Specialist",
    "Credit Analyst",
    "Deductions Analyst",
    "Dispute Management Analyst",
    "Collections Executive", # many "GL / R2R" reqs are AR-heavy
]

INCLUDE_HIDDEN_SEARCHES = True   # search HIDDEN_AR_TITLES too

# Use [""] for anywhere in India.
LOCATIONS = ["Bengaluru","Chennai"]

# Hard-drop any job whose location doesn't match LOCATIONS.
# Kept ON even in GET_EVERYTHING mode - you asked for these cities.
LOCATION_STRICT = True

SOURCES = ["naukri", "linkedin"]  # remove one to search only the other

# Your resume filename (must be in the SAME folder as this script).
# TIP: point this at an AR-tailored resume (cash application, collections,
# credit control, DSO, disputes) for the sharpest Match % / Missing Skills.
RESUME_PATH = "Jeevanandam_R_Senior AR.docx"

# Pages per role, per site. More pages = more coverage but slower + more
# likely to hit CAPTCHAs. With ~26 titles x 3 cities x 2 sites, 10 pages is
# already a long run - drop to 5 if it gets blocked, raise if you want more.
PAGES_PER_ROLE = 40

# Driven by POST_WINDOW_DAYS above (was 1).
MAX_DAYS_OLD = POST_WINDOW_DAYS

# Experience filter. Set this to the candidate's total years.
# (Auto-disabled when GET_EVERYTHING = True.)
EXPERIENCE_FILTER = True
MY_EXPERIENCE_YEARS = 5

# --- AR relevance ---------------------------------------------------
# Keep only jobs that read as Accounts Receivable (title/skills/description)
# at/above MIN_AR_PERCENT. Turn AR_ONLY off to keep all finance jobs.
# (Auto-disabled when GET_EVERYTHING = True.)
AR_ONLY = True
MIN_AR_PERCENT = 20     # drop jobs below this AR signal
HIDDEN_AR_MIN = 35        # AR % at/above this + non-AR title => "Hidden"
KEEP_AR_PERCENT = 30      # AR jobs at/above this survive the match cut-off

# --- Resume match -----------------------------------------------------
# (Auto-set to 0 when GET_EVERYTHING = True.)
MIN_MATCH_PERCENT = 60    # 0 to keep everything

# --- Applicants / competition ----------------------------------------
# For LinkedIn survivors, open the detail page to read the applicant count.
# Only runs on jobs that already passed all filters, so it's bounded.
ENRICH_LINKEDIN_APPLICANTS = True
# Drop jobs with MORE than this many known applicants. 0 = off (don't drop,
# just rank crowded ones lower). Unknown counts are never dropped here.
MAX_APPLICANTS = 0

# Drop postings whose Portal Pref heuristic (portal_preference()) is "Low"
# - i.e. reads as a stale/low-activity consultancy repost rather than a
# genuine active hire. (Auto-disabled when GET_EVERYTHING = True, to match
# its "keep every repost" promise.)
DROP_LOW_HIRING_SIGNAL = True

OUTPUT_FILE = "jobs.xlsx"

# (Auto-disabled when GET_EVERYTHING = True.)
FINANCE_ONLY = True
FINANCE_TERMS = [
    # AR / O2C (primary)
    "receivable", "collection", "cash application", "cash allocation",
    "order to cash", "o2c", "invoice to cash", "dso", "billing", "invoic",
    "credit control", "credit management", "ageing", "aging", "dispute",
    "dunning", "deduction", "chargeback", "remittance", "debtors",
    "unapplied cash", "lockbox",
    # general finance / accounting
    "finance", "financial", "account", "accounting", "ledger",
    "reconcil", "audit", "gaap", "ifrs", "month end", "close",
    "controller", "compliance", "sox",
    # Revenue
    "revenue", "revenue recognition", "deferred revenue", "accrual",
    # FP&A (adjacent, lower priority)
    "forecasting", "budgeting", "variance", "management reporting", "mis",
]

# Terms that identify genuine Accounts Receivable work, used for the AR %
# score and for spotting hidden opportunities.
AR_TERMS = [
    "accounts receivable", "receivable", "receivables", "a/r", "ar ageing",
    "ar aging", "order to cash", "order-to-cash", "o2c", "invoice to cash",
    "i2c", "cash application", "cash applications", "cash allocation",
    "cash posting", "receipt posting", "collections", "collection",
    "dunning", "credit control", "credit management", "credit risk",
    "credit limit", "credit review", "credit hold", "dso",
    "days sales outstanding", "aging", "ageing", "past due", "overdue",
    "dispute", "dispute management", "deduction", "deductions", "chargeback",
    "short payment", "short-payment", "remittance", "remittance advice",
    "billing", "invoicing", "invoice", "e-invoicing", "customer master",
    "bad debt", "doubtful debt", "provision for doubtful", "write-off",
    "write off", "unapplied cash", "unallocated cash", "on-account",
    "customer statement", "lockbox", "sundry debtors", "debtors",
    "ar reconciliation", "netting", "reconciliation", "month end close",
]

# Concrete skills/tools/certs used to fill the "Missing Skills in Resume"
# column. Nicely-cased for display; matched case-insensitively. Edit freely.
SKILL_VOCAB = [
    # Tools & systems
    "SAP", "SAP FSCM", "SAP S/4HANA", "SAP FI", "Oracle", "Oracle Fusion",
    "Oracle R12", "PeopleSoft", "NetSuite", "Microsoft Dynamics", "Tally",
    "QuickBooks", "Workday", "HighRadius", "BlackLine", "Esker", "Billtrust",
    "GetPaid", "Cforia", "Advanced Excel", "Power BI", "Power Query", "SQL",
    "VBA", "Macros", "Tableau",
    # AR / O2C concepts
    "Accounts Receivable", "Order to Cash", "O2C", "Cash Application",
    "Cash Allocation", "Cash Posting", "Collections", "Credit Control",
    "Credit Analysis", "Credit Risk Assessment", "Dispute Management",
    "Deductions Management", "Chargebacks", "AR Ageing", "AR Aging",
    "DSO Analysis", "Dunning", "Billing", "Invoicing", "E-Invoicing",
    "Bad Debt Provisioning", "Bank Reconciliation", "Account Reconciliation",
    "Customer Master Data", "Remittance Processing", "Lockbox",
    "Unapplied Cash", "Month-End Close", "Revenue Recognition",
    "Intercompany Reconciliation",
    # Accounting / controls
    "US GAAP", "IFRS", "SOX", "Internal Controls", "Journal Entries",
    "GL Reconciliation",
    # Certs / education
    "CA", "CMA", "CPA", "MBA Finance", "ACCA", "M.Com", "B.Com", "CA Inter",
]

# SHOW_BROWSER = True  -> visible window; you can solve CAPTCHAs.
# SHOW_BROWSER = False -> headless; runs even when the PC is LOCKED.
SHOW_BROWSER = False

USE_PERSISTENT_PROFILE = True
USER_DATA_DIR = "browser_profile"   # created next to this script

# Scoring weights (relative; they don't need to sum to 100).
WEIGHT_FIT = 40      # resume coverage of the job
WEIGHT_AR = 20       # how strongly the job reads as Accounts Receivable
WEIGHT_FRESH = 15    # recency
WEIGHT_LOWCOMP = 20  # few applicants = better
WEIGHT_HIRING = 5    # direct employer vs consultancy

CONSULTANCY_WORDS = [
    "consultanc", "consultant", "staffing", "recruit",
    "manpower", "placement", "hiring solutions",
    "hr services", "hr solutions", "talent solutions",
]

LOCATION_ALIASES = {
    "chennai":   {"chennai", "madras"},
    "bengaluru": {"bengaluru", "bangalore", "banglore", "bengalooru"},
    "bangalore": {"bengaluru", "bangalore", "banglore", "bengalooru"},
    "banglore":  {"bengaluru", "bangalore", "banglore", "bengalooru"},
    "mumbai":    {"mumbai", "bombay", "navi mumbai", "thane"},
    "hyderabad": {"hyderabad", "secunderabad"},
    "delhi":     {"delhi", "new delhi", "ncr"},
    "gurgaon":   {"gurgaon", "gurugram"},
    "noida":     {"noida", "greater noida"},
    "pune":      {"pune"},
    "kolkata":   {"kolkata", "calcutta"},
    "coimbatore": {"coimbatore"},
}

# ---- MASTER MODE overrides (auto-applied; leave as-is) ----
# When GET_EVERYTHING is on, relax every "drop" filter except location + date.
if GET_EVERYTHING:
    AR_ONLY = False          # don't drop jobs for low AR signal
    FINANCE_ONLY = False      # don't drop non-finance-worded titles
    EXPERIENCE_FILTER = False # keep every experience level
    MIN_AR_PERCENT = 0
    MIN_MATCH_PERCENT = 0     # keep every job regardless of resume overlap
    MAX_APPLICANTS = 0        # never drop crowded jobs
    DROP_LOW_HIRING_SIGNAL = False  # keep every repost, per the promise above
    # LOCATION_STRICT and MAX_DAYS_OLD stay as configured above.

# ===================== nothing to edit below =====================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Full set of titles actually searched.
SEARCH_ROLES = list(TARGET_ROLES)
if INCLUDE_HIDDEN_SEARCHES:
    for t in HIDDEN_AR_TITLES:
        if t not in SEARCH_ROLES:
            SEARCH_ROLES.append(t)


def resolve(path):
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.join(SCRIPT_DIR, path)


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")

STOPWORDS = set("""
a an the and or of to in for with on at by from is are was were be been being
as it its this that these those you your we our they them he she his her
i me my mine will would can could should may might must have has had do does did
not no yes so if then than but out up down over under again more most such own
job jobs role roles work working company companies experience year years month
""".split())

# Applicant-count patterns (used on cards and on LinkedIn detail pages).
APPLICANT_RE = re.compile(r"(\d[\d,]*)\s*applicant", re.I)
EARLY_RE = re.compile(r"first\s*\d+\s*applicant", re.I)

# Point-of-contact patterns (used on job descriptions when a poster leaves
# a direct email/phone instead of relying on the portal's "Apply" flow).
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)")
CONTACT_IGNORE_DOMAINS = (
    "naukri.com", "linkedin.com", "indeed.com", "noreply", "no-reply",
    "donotreply", "example.com",
)

# Phrases that read as an agency posting "on behalf of" an unnamed client -
# i.e. the portal listing itself is not the hiring company's own channel,
# so applying there reaches an intermediary, not the employer.
THIRD_PARTY_PHRASES = [
    "on behalf of our client", "on behalf of a client", "for our client",
    "one of our clients", "leading mnc client", "reputed client",
    "client of ours", "our client is hiring", "hiring for our client",
    "staffing partner", "recruitment partner", "manpower consultancy",
    "immediate requirement for our client", "confidential search",
]


def tokenize(text):
    if not text:
        return []
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.&]{1,}", text.lower())
    return [w for w in words if w not in STOPWORDS]


def read_resume_text(path):
    if not path:
        return ""
    full = resolve(path)
    try:
        lower = full.lower()
        if lower.endswith(".pdf"):
            import pdfplumber
            chunks = []
            with pdfplumber.open(full) as pdf:
                for page in pdf.pages:
                    chunks.append(page.extract_text() or "")
            return "\n".join(chunks)
        elif lower.endswith(".docx"):
            import docx
            document = docx.Document(full)
            return "\n".join(p.text for p in document.paragraphs)
        else:
            with open(full, encoding="utf-8", errors="ignore") as f:
                return f.read()
    except FileNotFoundError:
        print(f"  ! Resume not found at:\n      {full}")
        print("    -> Put your resume in the SAME folder as this script and set")
        print("       RESUME_PATH to its exact filename (with .pdf / .docx).")
        print("    Continuing with roles only.")
        return ""
    except Exception as e:
        print(f"  ! Could not read resume ({e}) - continuing with roles only.")
        return ""


def build_keywords(resume_text):
    keywords = set(tokenize(resume_text))
    for role in SEARCH_ROLES:
        for w in tokenize(role):
            keywords.add(w)
    return keywords


def clean(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_applicants(text):
    """
    Applicant count from arbitrary text, or None if not stated.
    'Be among the first 25 applicants' -> 0 (very early).
    'Over 200 applicants' / '25 applicants' -> the number.
    """
    if not text:
        return None
    low = text.lower()
    if "be among the first" in low or EARLY_RE.search(low):
        return 0
    m = APPLICANT_RE.search(low)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def extract_contact(text):
    """
    Direct email/phone left in a job description, if any - lets you skip the
    portal's apply flow and reach a person directly. '-' when none is found
    (most postings don't leave one; that's expected, not a bug).
    """
    if not text:
        return "-"
    emails = [e for e in EMAIL_RE.findall(text)
              if not any(d in e.lower() for d in CONTACT_IGNORE_DOMAINS)]
    phones = PHONE_RE.findall(text)
    parts = []
    if emails:
        parts.append(emails[0])
    if phones:
        parts.append(phones[0])
    return " | ".join(parts) if parts else "-"


def reads_third_party(text):
    low = (text or "").lower()
    return any(p in low for p in THIRD_PARTY_PHRASES)


def is_confidential_company(company):
    c = (company or "").strip().lower()
    return c in ("", "confidential", "confidential company",
                 "a reputed company", "reputed company", "leading company",
                 "reputed organisation", "reputed organization")


def jd_signature(job):
    """
    A short fingerprint of the job description, used to spot the same JD
    posted under many different 'company' names - the classic staffing-
    agency mass-repost pattern. Returns None when the description is too
    short to fingerprint reliably.
    """
    desc = (job.get("description") or "")[:300]
    sig = re.sub(r"\s+", " ", desc.lower()).strip()
    return sig if len(sig) >= 60 else None


def detect_employment_type(*texts):
    """
    Full-time / Part-time / Contract / Internship, read from the posting.
    Falls back to 'Full-time (assumed)' when nothing is stated - most AR
    roles are full-time, but we flag that it's an assumption.
    """
    hay = " ".join(t for t in texts if t).lower()
    if not hay.strip():
        return "Full-time (assumed)"
    if "intern" in hay:
        return "Internship"
    if ("part-time" in hay or "part time" in hay):
        return "Part-time"
    if ("contract" in hay or "temporary" in hay or "fixed term"
            in hay or "fixed-term" in hay or "c2h" in hay):
        return "Contract"
    if ("full-time" in hay or "full time" in hay or "permanent" in hay):
        return "Full-time"
    return "Full-time (assumed)"


def get_placeholder(job, ptype):
    for ph in job.get("placeholders", []):
        if ph.get("type") == ptype:
            return ph.get("label", "")
    return ""


def days_from_epoch(ms):
    if not ms:
        return None
    try:
        posted = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return (datetime.now(timezone.utc) - posted).days
    except Exception:
        return None


def days_from_iso(dt):
    if not dt:
        return None
    try:
        d = datetime.fromisoformat(dt[:10]).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except Exception:
        return None


def days_from_label(label):
    if not label:
        return None
    s = label.lower()
    if "just" in s or "today" in s or "few" in s or "hour" in s:
        return 0
    if "yesterday" in s:
        return 1
    m = re.search(r"(\d+)\+?\s*day", s)
    if m:
        return int(m.group(1))
    if "month" in s:
        m = re.search(r"(\d+)", s)
        return int(m.group(1)) * 30 if m else 30
    return None


def posting_date_str(days_old):
    """Actual calendar date the job was posted, from 'N days ago'."""
    if days_old is None:
        return "?"
    try:
        d = datetime.now() - timedelta(days=int(days_old))
        return d.strftime("%d-%b-%Y")
    except Exception:
        return "?"


def normalize_api_job(job):
    jd = job.get("jdURL", "")
    url = ("https://www.naukri.com" + jd) if jd.startswith("/") else jd
    ab = job.get("ambitionBoxData", {}) or {}
    days = days_from_epoch(job.get("createdDate"))
    if days is None:
        days = days_from_label(job.get("footerPlaceholderLabel", ""))
    # Naukri search API rarely exposes an applicant count; try a couple of
    # possible fields, else leave it unknown.
    applicants = None
    for key in ("applyCount", "numApplicants", "applicantCount"):
        val = job.get(key)
        if isinstance(val, (int, float)):
            applicants = int(val)
            break
    desc = clean(job.get("jobDescription", ""))
    title = clean(job.get("title", ""))
    etype = job.get("employmentType") or detect_employment_type(title, desc)
    return {
        "source": "Naukri",
        "job_id": "N:" + str(job.get("jobId", "")),
        "title": title,
        "company": clean(job.get("companyName", "")),
        "employment_type": etype,
        "experience": get_placeholder(job, "experience"),
        "salary": get_placeholder(job, "salary"),
        "location": get_placeholder(job, "location"),
        "skills": clean(job.get("tagsAndSkills", "").replace(",", ", ")),
        "description": desc,
        "days_old": days,
        "reviews": ab.get("ReviewsCount") or "",
        "applicants": applicants,
        "url": url,
    }


def normalize_naukri_card(card):
    def txt(sel):
        el = card.query_selector(sel)
        return el.inner_text().strip() if el else ""

    def attr(sel, a):
        el = card.query_selector(sel)
        return el.get_attribute(a) if el else ""

    title = txt("a.title")
    if not title:
        return None
    skills = ", ".join(
        s.inner_text().strip() for s in card.query_selector_all("ul.tags-gt li"))
    href = attr("a.title", "href") or ""
    try:
        card_text = card.inner_text() or ""
    except Exception:
        card_text = ""
    desc = clean(txt("span.job-desc"))
    return {
        "source": "Naukri",
        "job_id": "N:" + (href or title),
        "title": clean(title),
        "company": clean(txt("a.comp-name") or txt("a.subTitle")),
        "employment_type": detect_employment_type(title, desc, skills, card_text),
        "experience": clean(txt("span.expwdth") or txt(".exp")),
        "salary": clean(txt("span.sal") or txt(".sal-wrap span")),
        "location": clean(txt("span.locWdth") or txt(".loc")),
        "skills": clean(skills),
        "description": desc,
        "days_old": days_from_label(txt("span.job-post-day")),
        "reviews": "",
        "applicants": extract_applicants(card_text),
        "url": href,
    }


def normalize_linkedin_card(card):
    def txt(sel):
        el = card.query_selector(sel)
        return el.inner_text().strip() if el else ""

    def attr(sel, a):
        el = card.query_selector(sel)
        return el.get_attribute(a) if el else ""

    title = txt("h3.base-search-card__title")
    if not title:
        return None
    href = (attr("a.base-card__full-link", "href")
            or attr("a", "href") or "").split("?")[0]
    try:
        card_text = card.inner_text() or ""
    except Exception:
        card_text = ""
    return {
        "source": "LinkedIn",
        "job_id": "L:" + (href or title),
        "title": clean(title),
        "company": clean(txt("h4.base-search-card__subtitle")),
        "employment_type": "",   # filled from the detail page during enrichment
        "experience": "",
        "salary": "",
        "location": clean(txt("span.job-search-card__location")),
        "skills": "",
        "description": "",
        "days_old": days_from_iso(attr("time", "datetime")),
        "reviews": "",
        "applicants": extract_applicants(card_text),
        "url": href,
    }


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def naukri_url(keyword, location, page):
    kw = slugify(keyword)
    base = (f"https://www.naukri.com/{kw}-jobs-in-{slugify(location)}"
            if location else f"https://www.naukri.com/{kw}-jobs")
    return base if page == 1 else f"{base}-{page}"


def linkedin_url(keyword, location, start):
    loc = location or "India"
    return ("https://www.linkedin.com/jobs-guest/jobs/api/"
            f"seeMoreJobPostings/search?keywords={quote(keyword)}"
            f"&location={quote(loc)}&start={start}")


def linkedin_detail_url(job_id):
    return f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


def open_browser(p, headless):
    common = dict(user_agent=UA, viewport={"width": 1366, "height": 900},
                  locale="en-US")
    if USE_PERSISTENT_PROFILE:
        data_dir = resolve(USER_DATA_DIR)
        os.makedirs(data_dir, exist_ok=True)
        for channel in ("msedge", "chrome"):
            try:
                ctx = p.chromium.launch_persistent_context(
                    data_dir, headless=headless, channel=channel, **common)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                return page, ctx.close
            except Exception:
                continue
        ctx = p.chromium.launch_persistent_context(
            data_dir, headless=headless, **common)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        return page, ctx.close

    browser = None
    for channel in ("msedge", "chrome"):
        try:
            browser = p.chromium.launch(headless=headless, channel=channel)
            break
        except Exception:
            continue
    if browser is None:
        browser = p.chromium.launch(headless=headless)
    ctx = browser.new_context(**common)
    return ctx.new_page(), browser.close


def fetch_naukri_page(page, url):
    try:
        with page.expect_response(
            lambda r: "jobapi/v3/search" in r.url and r.status == 200,
            timeout=30000,
        ) as info:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        data = info.value.json()
        raw = data.get("jobDetails", []) or []
        if raw:
            return [normalize_api_job(j) for j in raw]
    except Exception:
        pass
    try:
        page.wait_for_timeout(3000)
        cards = page.query_selector_all(
            "div.srp-jobtuple-wrapper, article.jobTuple")
        return [j for j in (normalize_naukri_card(c) for c in cards) if j]
    except Exception as e:
        print(f"    (Naukri read error: {e})")
        return []


def fetch_linkedin_page(page, keyword, location, start):
    try:
        page.goto(linkedin_url(keyword, location, start),
                  wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        cards = page.query_selector_all(
            "div.base-card, div.job-search-card, li")
        return [j for j in (normalize_linkedin_card(c) for c in cards) if j]
    except Exception as e:
        print(f"    (LinkedIn read error: {e})")
        return []


def fetch_all():
    from playwright.sync_api import sync_playwright
    results = []
    combos = len(SEARCH_ROLES) * len(LOCATIONS) * len(SOURCES)
    print(f"Searching {len(SEARCH_ROLES)} titles x {len(LOCATIONS)} cities "
          f"x {len(SOURCES)} sites = {combos} combinations, "
          f"up to {PAGES_PER_ROLE} pages each.\n")
    with sync_playwright() as p:
        page, close = open_browser(p, headless=not SHOW_BROWSER)
        try:
            page.goto("https://www.naukri.com/", wait_until="domcontentloaded",
                      timeout=45000)
        except Exception:
            pass
        if SHOW_BROWSER:
            input("\n>> A browser window has opened.\n"
                  "   If you see a CAPTCHA, cookie banner, or login popup,\n"
                  "   deal with it in that window, then come back here and\n"
                  "   press ENTER to start searching...\n")

        for role in SEARCH_ROLES:
            for loc in LOCATIONS:
                if "naukri" in SOURCES:
                    print(f"[Naukri]   {role} in {loc or 'India'}")
                    for pg in range(1, PAGES_PER_ROLE + 1):
                        got = fetch_naukri_page(page, naukri_url(role, loc, pg))
                        print(f"    page {pg}: {len(got)} jobs")
                        if not got:
                            break
                        results.extend(got)
                        time.sleep(2)
                if "linkedin" in SOURCES:
                    print(f"[LinkedIn] {role} in {loc or 'India'}")
                    for pg in range(1, PAGES_PER_ROLE + 1):
                        got = fetch_linkedin_page(page, role, loc, (pg - 1) * 25)
                        print(f"    page {pg}: {len(got)} jobs")
                        if not got:
                            break
                        results.extend(got)
                        time.sleep(2)
        close()
    return results


def enrich_linkedin_applicants(jobs):
    """
    For LinkedIn jobs that survived filtering, open the guest detail page and
    read the real applicant count, the description (sharpens scoring +
    missing-skills), and the employment type. Bounded to survivors only.
    """
    targets = [j for j in jobs if j["source"] == "LinkedIn"
               and re.search(r"\d{6,}", j.get("url", ""))]
    if not targets:
        return
    from playwright.sync_api import sync_playwright
    print(f"\nEnriching {len(targets)} LinkedIn jobs "
          f"(applicants, description, job type)...")
    with sync_playwright() as p:
        page, close = open_browser(p, headless=not SHOW_BROWSER)
        for i, job in enumerate(targets, 1):
            m = re.search(r"(\d{6,})", job["url"])
            if not m:
                continue
            try:
                page.goto(linkedin_detail_url(m.group(1)),
                          wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(700)
                el = page.query_selector(
                    "span.num-applicants__caption, "
                    "figure.num-applicants__figure, "
                    "span.jobs-unified-top-card__applicant-count")
                if el:
                    n = extract_applicants(el.inner_text())
                    if n is not None:
                        job["applicants"] = n
                if not job["description"]:
                    d = page.query_selector(
                        "div.show-more-less-html__markup, div.description__text")
                    if d:
                        job["description"] = clean(d.inner_text())
                # Employment type sits in the "job criteria" list.
                if not job.get("employment_type"):
                    crit = page.query_selector_all(
                        "li.description__job-criteria-item")
                    etype = ""
                    for c in crit:
                        label = (c.query_selector(
                            "h3.description__job-criteria-subheader"))
                        value = (c.query_selector(
                            "span.description__job-criteria-text"))
                        if label and value and "employment type" in \
                                label.inner_text().strip().lower():
                            etype = clean(value.inner_text())
                            break
                    job["employment_type"] = etype or detect_employment_type(
                        job["title"], job["description"])
            except Exception:
                pass
            if i % 10 == 0:
                print(f"    ...{i}/{len(targets)}")
            time.sleep(1)
        close()


# ---------- Scoring ----------

def score_fit(job, resume_keywords):
    """Fraction (0..1) of a job's meaningful terms your resume covers."""
    fields = [(job["title"], 3.0), (job["skills"], 2.0), (job["description"], 1.0)]
    weighted_total = 0.0
    weighted_matched = 0.0
    for text, w in fields:
        toks = set(tokenize(text))
        if not toks:
            continue
        weighted_total += w * len(toks)
        weighted_matched += w * len(toks & resume_keywords)
    if weighted_total == 0:
        return 0.0
    base = weighted_matched / weighted_total
    if any(role.lower() in job["title"].lower() for role in TARGET_ROLES):
        base = min(1.0, base + 0.15)
    return base


def score_ar(job):
    """0..1: how strongly this job reads as Accounts Receivable (title
    weighted heaviest)."""
    hay = " ".join([job["title"], job["skills"], job["description"]]).lower()
    if not hay.strip():
        return 0.0
    hits = sum(1 for t in AR_TERMS if t in hay)
    title = job["title"].lower()
    title_hits = sum(1 for t in AR_TERMS if t in title)
    raw = hits + title_hits * 2          # title mentions count double
    return min(1.0, raw / 6.0)           # ~6 signal points = full marks


def missing_skills(job, resume_keywords):
    """
    Concrete tools/skills the JD asks for that your resume does NOT mention.
    This is your tailoring to-do list for that job. Returns a display string.
    """
    hay = " ".join([job["title"], job["skills"], job["description"]]).lower()
    if not hay.strip():
        return "-"
    missing = []
    for term in SKILL_VOCAB:
        tl = term.lower()
        # Whole-term match (word boundaries) so short terms like "CA"/"O2C"
        # don't false-match inside "foreCAst"/"speciFIc" etc.
        pattern = r"(?<![a-z0-9])" + re.escape(tl) + r"(?![a-z0-9])"
        if re.search(pattern, hay):
            toks = tokenize(term)
            # Missing if any word of the term isn't already in the resume.
            if toks and not all(t in resume_keywords for t in toks):
                if term not in missing:
                    missing.append(term)
    return ", ".join(missing) if missing else "None - strong match"


def is_literal_ar_title(title):
    t = title.lower()
    return ("accounts receivable" in t or "receivable" in t
            or re.search(r"(?<![a-z])a/?r(?![a-z])", t) is not None
            or "order to cash" in t or "o2c" in t
            or "collections" in t or "credit control" in t
            or "cash application" in t)


def classify_hidden(job, ar_ratio):
    """Hidden AR = strong AR content but a title that doesn't say AR."""
    return (ar_ratio * 100 >= HIDDEN_AR_MIN
            and not is_literal_ar_title(job["title"]))


def score_fresh(job):
    d = job["days_old"]
    if d is None:
        return 0.4
    if d <= 0:
        return 1.0
    if d >= MAX_DAYS_OLD:
        return 0.0
    return 1.0 - (d / MAX_DAYS_OLD)


def score_lowcomp(job):
    """
    1.0 = few/no applicants (great), down to ~0.1 = crowded.
    When the count is unknown, fall back to freshness as a proxy: a post
    that's only hours old is usually still early.
    """
    n = job.get("applicants")
    if n is None:
        d = job["days_old"]
        if d is None:
            return 0.4
        return 0.85 if d <= 0 else 0.3
    if n <= 0:
        return 1.0          # "be among the first N applicants"
    if n <= 10:
        return 0.9
    if n <= 25:
        return 0.7
    if n <= 50:
        return 0.45
    if n <= 100:
        return 0.25
    return 0.1


def is_consultancy(company):
    c = company.lower()
    return any(w in c for w in CONSULTANCY_WORDS)


def score_hiring(job):
    # Recruiter/consultancy posts are KEPT and only mildly deprioritised
    # (0.4 vs 0.7) so they surface instead of being buried at the bottom.
    s = 0.4 if is_consultancy(job["company"]) else 0.7
    try:
        if float(str(job["reviews"]).replace(",", "")) > 50:
            s += 0.3
    except Exception:
        pass
    return min(1.0, s)


def portal_preference(job):
    """
    HONEST estimate of whether the company/recruiter behind this posting
    actually works this portal - vs. it being a staffing-agency repost
    where applying just lands in a middleman's inbox. No portal exposes
    this for real, so this is a heuristic from:
      - direct employer vs consultancy name vs confidential/unnamed employer
        vs "on behalf of our client" language in the JD itself,
      - the same JD text posted under many different company names
        (mass-repost pattern),
      - how fresh the posting is,
      - whether people are actively applying (activity = a live req).
    Returns (label, reason).
    """
    fresh = job["days_old"]
    n = job.get("applicants")
    consult = is_consultancy(job["company"])
    confidential = is_confidential_company(job["company"])
    third_party = reads_third_party(job.get("description", ""))
    dup_count = job.get("_dup_company_count", 1)

    reasons = []
    score = 0
    if not consult and not confidential and not third_party:
        score += 2
        reasons.append("named direct employer")
    elif confidential:
        reasons.append("confidential/unnamed employer")
    elif third_party:
        reasons.append("JD reads as agency-on-behalf-of-client")
    else:
        reasons.append("consultancy/staffing name")
    if dup_count >= 3:
        score -= 2
        reasons.append(f"same JD posted under {dup_count} company names")
    if fresh is not None and fresh <= 1:
        score += 2
        reasons.append("posted <=1 day")
    elif fresh is not None and fresh <= 3:
        score += 1
        reasons.append("posted <=3 days")
    else:
        reasons.append("older post")
    if n is not None and n >= 1:
        score += 1
        reasons.append("active applicants")
    # Naukri postings from a named, non-agency company are almost always
    # genuinely sourced/managed on Naukri itself.
    if job["source"] == "Naukri" and not consult and not confidential:
        score += 1

    if score >= 4:
        label = "High"
    elif score >= 2:
        label = "Medium"
    else:
        label = "Low"
    return label, "; ".join(reasons)


def score_job(job, keywords):
    fit = score_fit(job, keywords)
    ar = job.get("_ar_ratio")
    if ar is None:
        ar = score_ar(job)
    fresh = score_fresh(job)
    lowcomp = score_lowcomp(job)
    hiring = score_hiring(job)
    total = (fit * WEIGHT_FIT + ar * WEIGHT_AR + fresh * WEIGHT_FRESH
             + lowcomp * WEIGHT_LOWCOMP + hiring * WEIGHT_HIRING)
    pref_label, pref_reason = portal_preference(job)
    return {
        "score": round(total),
        "fit": round(fit * 100),
        "ar": round(ar * 100),
        "fresh": round(fresh * 100),
        "lowcomp": round(lowcomp * 100),
        "hiring": round(hiring * 100),
        "hidden": "Yes" if classify_hidden(job, ar) else "",
        "missing": missing_skills(job, keywords),
        "posted_date": posting_date_str(job["days_old"]),
        "portal_pref": pref_label,
        "portal_pref_reason": pref_reason,
        "contact": extract_contact(job.get("description", "")),
    }


# ---------- Filters ----------

def parse_exp_range(label):
    if not label:
        return None
    nums = re.findall(r"\d+", label)
    if not nums:
        return None
    if "+" in label and len(nums) == 1:
        return (int(nums[0]), 99)
    if len(nums) == 1:
        return (int(nums[0]), int(nums[0]))
    return (int(nums[0]), int(nums[1]))


def passes_experience(job):
    if not EXPERIENCE_FILTER:
        return True
    rng = parse_exp_range(job.get("experience", ""))
    if rng is None:
        return True
    lo, hi = rng
    return (lo - 1) <= MY_EXPERIENCE_YEARS <= (hi + 2)


def wanted_location_tokens():
    tokens = set()
    for loc in LOCATIONS:
        key = (loc or "").strip().lower()
        if not key:
            continue
        tokens |= LOCATION_ALIASES.get(key, {key})
    return tokens


def passes_location(job, wanted):
    if not LOCATION_STRICT or not wanted:
        return True
    loc = (job.get("location") or "").lower()
    if not loc:
        return False
    return any(tok in loc for tok in wanted)


def passes_finance(job):
    if not FINANCE_ONLY:
        return True
    hay = (job["title"] + " " + job["skills"]).lower()
    return any(t in hay for t in FINANCE_TERMS)


def write_excel(jobs, path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    # ---- Columns (Posted By / Point of Contact added after Company) ----
    headers = ["Portal", "Role", "Company", "Posted By", "Point of Contact",
               "Type (FT/PT)", "Location", "Experience", "Salary",
               "Applicants", "Posting Date", "Resume Match %",
               "Missing Skills in Resume", "Portal Pref (est.)",
               "Portal Pref Reason", "Job Link",
               # kept helper columns (right side) for your visibility:
               "AR %", "Hidden?", "Score"]
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F5496")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"

    for job in jobs:
        n = job.get("applicants")
        appl_display = "?" if n is None else ("Early" if n <= 0 else n)
        posted_by = "Recruiter" if is_consultancy(job["company"]) else "Direct"
        ws.append([
            job["source"],                              # 1  Portal
            job["title"],                               # 2  Role
            job["company"],                             # 3  Company
            posted_by,                                  # 4  Posted By
            job.get("contact", "-"),                     # 5  Point of Contact
            job.get("employment_type") or "-",          # 6  Type
            job["location"],                            # 7  Location
            job["experience"] or "-",                   # 8  Experience
            job["salary"] or "Not disclosed",           # 9  Salary
            appl_display,                               # 10 Applicants
            job["posted_date"],                         # 11 Posting Date
            job["fit"],                                 # 12 Resume Match %
            job["missing"],                             # 13 Missing Skills
            job["portal_pref"],                         # 14 Portal Pref
            job.get("portal_pref_reason", ""),          # 15 Portal Pref Reason
            job["url"],                                 # 16 Job Link
            job["ar"],                                  # 17 AR %
            job["hidden"],                              # 18 Hidden?
            job["score"],                               # 19 Score
        ])
        r = ws.max_row

        # Posted By colour (col 4): recruiter = amber, direct = green
        pb_color = "FFF2CC" if posted_by == "Recruiter" else "E2EFDA"
        ws.cell(row=r, column=4).fill = PatternFill("solid", fgColor=pb_color)

        # Point of Contact colour (col 5): highlight when a direct email/
        # phone was actually found in the JD.
        if job.get("contact", "-") != "-":
            ws.cell(row=r, column=5).fill = PatternFill("solid", fgColor="C6EFCE")

        # Applicants colour (col 10): green = few, red = many
        if n is not None:
            acolor = ("C6EFCE" if n <= 25 else "FFEB9C" if n <= 100 else "FFC7CE")
            ws.cell(row=r, column=10).fill = PatternFill("solid", fgColor=acolor)

        # Resume Match % colour (col 12)
        m = job["fit"]
        mcolor = "C6EFCE" if m >= 75 else "FFEB9C" if m >= 60 else "FFC7CE"
        ws.cell(row=r, column=12).fill = PatternFill("solid", fgColor=mcolor)

        # Portal Pref colour (col 14)
        pref = job["portal_pref"]
        pcolor = ("C6EFCE" if pref == "High" else
                  "FFEB9C" if pref == "Medium" else "FFC7CE")
        ws.cell(row=r, column=14).fill = PatternFill("solid", fgColor=pcolor)

        # AR % colour (col 17)
        f = job["ar"]
        fcolor = "C6EFCE" if f >= 60 else "FFEB9C" if f >= 35 else "FFC7CE"
        ws.cell(row=r, column=17).fill = PatternFill("solid", fgColor=fcolor)

        # Hidden? highlight (col 18)
        if job["hidden"]:
            ws.cell(row=r, column=18).fill = PatternFill("solid", fgColor="D9E1F2")

        # Score colour (col 19)
        s = job["score"]
        scolor = "C6EFCE" if s >= 70 else "FFEB9C" if s >= 45 else "FFC7CE"
        ws.cell(row=r, column=19).fill = PatternFill("solid", fgColor=scolor)

        # Job Link hyperlink (col 16)
        link = ws.cell(row=r, column=16)
        if job["url"]:
            link.hyperlink = job["url"]
            link.value = "Open"
            link.font = Font(color="0563C1", underline="single")

    # Column widths (19 columns)
    widths = [9, 34, 24, 11, 22, 16, 15, 12, 16, 11, 13, 9, 40, 12, 30, 9, 8, 8, 7]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    # Wrap the long "Missing Skills" (col 13) and "Portal Pref Reason" (col 15)
    for row in ws.iter_rows(min_row=2, min_col=13, max_col=13):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=2, min_col=15, max_col=15):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def main():
    mode = "ALL JOBS (GET_EVERYTHING)" if GET_EVERYTHING else "strict AR-only"
    print(f"Mode: {mode}  |  window: last {MAX_DAYS_OLD} days  |  "
          f"cities: {', '.join(l for l in LOCATIONS if l) or 'India'}\n")

    print("Reading your resume...")
    resume_text = read_resume_text(RESUME_PATH)
    keywords = build_keywords(resume_text)
    print(f"  Matching against {len(keywords)} keywords.")
    if not resume_text:
        print("  (No resume text - Match % / Missing Skills based on roles only.)")
    print()

    raw_jobs = fetch_all()

    wanted = wanted_location_tokens()
    seen, jobs = set(), []
    dropped_loc = dropped_fin = dropped_exp = dropped_old = dropped_ar = 0
    for job in raw_jobs:
        jid = job["job_id"]
        if not jid or jid in seen:
            continue
        seen.add(jid)
        if job["days_old"] is not None and job["days_old"] > MAX_DAYS_OLD:
            dropped_old += 1
            continue

        ar_ratio = score_ar(job)
        job["_ar_ratio"] = ar_ratio

        # Keep if it's finance OR clearly AR (so hidden roles whose title
        # lacks a finance keyword aren't wrongly dropped).
        if (FINANCE_ONLY and not passes_finance(job)
                and ar_ratio * 100 < MIN_AR_PERCENT):
            dropped_fin += 1
            continue
        if AR_ONLY and ar_ratio * 100 < MIN_AR_PERCENT:
            dropped_ar += 1
            continue
        if not passes_experience(job):
            dropped_exp += 1
            continue
        if not passes_location(job, wanted):
            dropped_loc += 1
            continue
        jobs.append(job)

    # Optional: pull real applicant counts + job type for LinkedIn survivors.
    if ENRICH_LINKEDIN_APPLICANTS:
        try:
            enrich_linkedin_applicants(jobs)
        except Exception as e:
            print(f"  (LinkedIn enrichment skipped: {e})")

    # Optional hard cap on known applicant counts.
    if MAX_APPLICANTS and MAX_APPLICANTS > 0:
        before = len(jobs)
        jobs = [j for j in jobs
                if j.get("applicants") is None
                or j["applicants"] <= MAX_APPLICANTS]
        dropped_crowded = before - len(jobs)
    else:
        dropped_crowded = 0

    # Flag the same JD text posted under many different company names - the
    # classic staffing-agency mass-repost pattern - before scoring, so
    # portal_preference() can see it.
    sig_companies = {}
    for job in jobs:
        sig = jd_signature(job)
        job["_jd_sig"] = sig
        if sig:
            sig_companies.setdefault(sig, set()).add(job["company"].lower())
    for job in jobs:
        sig = job.get("_jd_sig")
        job["_dup_company_count"] = len(sig_companies[sig]) if sig else 1

    # Score everything (also computes Missing Skills, Posting Date, Portal Pref).
    for job in jobs:
        job.update(score_job(job, keywords))

    # Drop postings that don't look like genuine active hiring (consultancy
    # reposts / stale / no applicant activity) per portal_preference().
    if DROP_LOW_HIRING_SIGNAL:
        before_lowsignal = len(jobs)
        jobs = [j for j in jobs if j["portal_pref"] != "Low"]
        dropped_lowsignal = before_lowsignal - len(jobs)
    else:
        dropped_lowsignal = 0

    # Resume-match cut-off, but EXEMPT strong AR jobs so hidden ones survive.
    before_match = len(jobs)
    jobs = [j for j in jobs
            if j["fit"] >= MIN_MATCH_PERCENT or j["ar"] >= KEEP_AR_PERCENT]
    dropped_match = before_match - len(jobs)

    # Surface fresh, low-competition, good-fit jobs first.
    jobs.sort(key=lambda j: (j["score"], j["lowcomp"], j["fresh"], j["ar"],
                             j["fit"]), reverse=True)

    hidden_count = sum(1 for j in jobs if j["hidden"])
    recruiter_count = sum(1 for j in jobs if is_consultancy(j["company"]))
    print(f"\nFilter summary:")
    print(f"  dropped (too old)      : {dropped_old}")
    print(f"  dropped (not finance)  : {dropped_fin}")
    print(f"  dropped (< {MIN_AR_PERCENT}% AR)     : {dropped_ar}")
    print(f"  dropped (experience)   : {dropped_exp}")
    print(f"  dropped (wrong city)   : {dropped_loc}")
    print(f"  dropped (too crowded)  : {dropped_crowded}")
    print(f"  dropped (low hiring signal): {dropped_lowsignal}")
    print(f"  dropped (< {MIN_MATCH_PERCENT}% match) : {dropped_match}")
    print(f"  KEPT                   : {len(jobs)}  "
          f"(hidden AR: {hidden_count}, recruiter posts: {recruiter_count})")

    out = resolve(OUTPUT_FILE)
    try:
        write_excel(jobs, out)
        saved = out
    except PermissionError:
        stamp = datetime.now().strftime("%H%M%S")
        saved = resolve(OUTPUT_FILE.replace(".xlsx", "") + "_" + stamp + ".xlsx")
        print(f"  ! '{OUTPUT_FILE}' looks open in Excel - saving as "
              f"'{os.path.basename(saved)}' instead.")
        write_excel(jobs, saved)
    print(f"\nDone! Saved {len(jobs)} ranked jobs to:\n  {saved}")


if __name__ == "__main__":
    main()
