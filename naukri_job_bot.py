"""
JOB BOT  -  India finance jobs from Naukri + LinkedIn + Workday
===============================================================
Setup once:   pip install playwright openpyxl
              playwright install
Then run:     python naukri_job_bot.py

The Excel file is written into THIS script's folder. No resume is read
here - see "SCORING IS RESUME-FREE" below.

WHAT THIS BUILD IS FOR
----------------------
Collect every finance and accounting role being hired for right now
across the major Indian metros, tag each with its job family
(receivables, payables, ledger, audit, tax, treasury, payroll, FP&A,
credit, accounting), and publish the lot to a public page where any
visitor filters it down and scores it against their OWN resume.

IT USED TO BE NARROWER, WHICH EXPLAINS SOME OF THIS CODE
--------------------------------------------------------
This began as a one-person tool: AR roles only, 10-15 LPA, two cities,
and only jobs clearing an interview-odds bar. Those cuts deleted roughly
1,000 of every 1,300 rows collected. They are gone from the website path
- the site publishes everything and lets the reader choose - but the
machinery survives because the OWNER's spreadsheet still uses it:

  * finance_gate()    replaced ar_gate(). It rejects only genuine
                      non-finance work, and tags the rest by family.
  * SALARY_IS_A_GATE  is False, so salary is collected and shown but
                      never filters. Set True for the old behaviour.
  * INTERVIEW_MIN_SCORE now only splits the Excel file's two sheets.
                      It has no effect on what the website shows.

The interview-odds model still scores every row. On the site it is the
secondary number, behind each visitor's resume match.

SCORING IS RESUME-FREE
----------------------
What gets collected is decided entirely by the keyword stems in
PRIMARY_QUERIES / HIDDEN_QUERIES / LINKEDIN_QUERIES - a resume has never
had a say in that. The published SCORE used to be a different story: it
carried a 12% "does the owner's CV cover this JD" term, and the Missing
Skills column meant "asked for, but absent from the owner's CV". Both
were meaningless to every visitor except one person, so both are gone.

Interview odds are now directness, freshness, competition, family fit and
experience band only, and Missing Skills lists everything the posting
asks for. Resume matching happens in one place and one place only: the
visitor's own upload, scored in their own browser, on their own machine.

WHY IT IS ~30x FASTER THAN THE OLD BUILD
----------------------------------------
The old version did 4,000 full browser page-loads back to back, each
followed by a 2-second sleep. This version:
  * never navigates for search - it calls Naukri's own JSON API and
    LinkedIn's guest API with in-page fetch(), same-origin, cookies intact;
  * runs those fetches CONCURRENTLY inside the browser (Promise pool);
  * pulls 100 Naukri results per request instead of 20, so 3 requests
    replace 15;
  * uses ~15 broad queries instead of 25 near-duplicate long titles
    (Naukri/LinkedIn keyword search already expands them);
  * enriches only the shortlist, in parallel batches, not every survivor.
Typical run: 2-4 minutes instead of 2-3 hours.
"""

import os
import re
import html
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


# ============================ CONFIG ============================
# Everything you would realistically want to tune lives in this box.

# --- Money -------------------------------------------------------------
# NOT a gate any more. The site serves whoever opens it, and their band is
# not yours - so every salary is collected and published, and the page
# gives the visitor a slider instead. These two values only still drive
# Naukri's server-side facet when SALARY_IS_A_GATE is on, and the odds
# model's "is this worth your time" penalty for the owner's own view.
SALARY_IS_A_GATE = False
SALARY_MIN_LPA = 10.0
SALARY_MAX_LPA = 15.0
# A little slack so "8-12 Lacs" (negotiable up) isn't thrown away.
SALARY_TOLERANCE_LPA = 1.0
# Most Indian AR postings hide the CTC. Dropping all of them would delete
# ~70% of genuine jobs, so instead we keep an undisclosed posting ONLY when
# its experience band implies your bracket, and mark it "Est." in the sheet.
KEEP_UNDISCLOSED_SALARY = True

# --- You ---------------------------------------------------------------
# The only personal input left. It sets the experience band the odds
# model scores against; no resume is read anywhere in this script.
MY_EXPERIENCE_YEARS = 5

# --- Where ------------------------------------------------------------
# Every metro that does meaningful finance hiring in India. The page lets
# a visitor filter down to the one they care about, so collecting broadly
# here costs run time but never costs them relevance.
LOCATIONS = ["Bengaluru", "Chennai", "Hyderabad", "Mumbai", "Pune",
             "Delhi", "Gurgaon", "Noida", "Kolkata", "Coimbatore",
             "Ahmedabad", "Jaipur", "Kochi", "Chandigarh", "Indore"]
LOCATION_STRICT = True

# --- How fresh (hard gate) --------------------------------------------
# Interview odds collapse after ~a week: the recruiter has a pipeline by then.
MAX_DAYS_OLD = 7

# --- The shortlist bar -------------------------------------------------
# Still used to split the Excel file into "Shortlist" and "Near Misses"
# for the owner. It does NOT decide what reaches the website any more -
# the site publishes everything and lets the visitor sort by their own
# resume match, which is the whole point of the filters on the page.
INTERVIEW_MIN_SCORE = 0
# Minimum family-content signal (0-100) for a job whose TITLE doesn't name
# its family. A long JD mentioning "invoice" once scores ~33, which is why
# a low bar lets unrelated roles through - see finance_gate().
MIN_FAMILY_PERCENT = 30
# Postings from "Confidential" / unnamed employers almost never convert.
REQUIRE_NAMED_COMPANY = True
# Drop postings whose JD is too thin to tell what the job actually is.
MIN_DESCRIPTION_CHARS = 120

# --- Coverage vs speed -------------------------------------------------
SOURCES = ["naukri", "linkedin", "workday"]
NAUKRI_PAGES = 15            # x 100 results = up to 300 per query per city
NAUKRI_RESULTS_PER_PAGE = 100
# LinkedIn's guest API returns 10 cards per call (NOT 25 - stepping `start`
# by 25 silently skips 15 jobs every page).
LINKEDIN_PAGE_SIZE = 10
LINKEDIN_PAGES = 15          # x 10 results
# Push the salary / recency / experience cuts onto Naukri's own search
# facets instead of downloading everything and filtering here. Measured: it
# turns "12 of 41 disclosed CTCs in band" into "27 of 27". Set False only if
# a run comes back suspiciously empty and you want the unfiltered firehose.
NAUKRI_USE_PORTAL_FILTERS = True
# Concurrent in-browser fetches. Naukri is fine at 8. LinkedIn's guest API
# 429s aggressively - 2 is the sweet spot, higher just loses pages.
NAUKRI_CONCURRENCY = 8
LINKEDIN_CONCURRENCY = 2
LINKEDIN_RETRIES = 2        # re-request 429'd pages after a backoff
LINKEDIN_PACE_SECONDS = 1.0  # gap between bursts; LinkedIn throttles bursts,
                             # not volume - pacing recovers many calls
# Hard wall-clock caps. LinkedIn's guest API will happily 429 forever, and
# chasing the last few pages once tripled a 90s run. Naukri is the primary
# source for Indian AR roles; LinkedIn is a bonus, so it gets a budget.
LINKEDIN_SEARCH_BUDGET_S = 180
LINKEDIN_DETAIL_BUDGET_S = 180
DETAIL_CONCURRENCY = 6       # Naukri detail fetches
LINKEDIN_DETAIL_CONCURRENCY = 2   # LinkedIn 429s above this and we lose the
                                  # applicant counts entirely
# Pull the FULL job description for the shortlist (sharper AR %, missing
# skills, and it's where recruiters leave direct emails/phone numbers).
ENRICH_SHORTLIST = True
# The resume match on the site is only as good as the text behind it, and
# a card with no description can only ever be matched on its title. So the
# cap is now high enough to cover a whole run rather than the old top-120.
# This is the single biggest cost in wall-clock time, and it buys the one
# feature the page is actually for.
ENRICH_LIMIT = 4000
# Naukri and Workday detail fetches are cheap and reliable; LinkedIn's are
# neither, so it keeps a separate, smaller ceiling and its own time budget.
LINKEDIN_ENRICH_LIMIT = 400

# --- Browser -----------------------------------------------------------
SHOW_BROWSER = False        # True once if a portal starts asking for CAPTCHA
USE_PERSISTENT_PROFILE = True
USER_DATA_DIR = "browser_profile"

OUTPUT_FILE = "jobs.xlsx"

# --- Search queries ----------------------------------------------------
# Broad keyword stems, NOT full job titles. Portal search already expands
# "accounts receivable" into Analyst / Specialist / Executive / Senior etc,
# so listing those separately just multiplies runtime for the same rows.
# One or two stems per finance family, not full job titles - portal search
# already expands "accounts payable" into Analyst / Specialist / Executive /
# Senior, so listing those separately multiplies run time for the same rows.
PRIMARY_QUERIES = [
    # receivable side
    "accounts receivable",
    "order to cash",
    "collections analyst",
    "credit control",
    # payable side
    "accounts payable",
    "procure to pay",
    "invoice processing",
    # core accounting / close
    "general ledger",
    "record to report",
    "financial reporting",
    "accountant",
    "finance executive",
    # analysis & planning
    "financial analyst",
    "fp&a analyst",
    # specialisms
    "internal audit",
    "taxation",
    "treasury",
    "payroll",
    "bank reconciliation",
    "finance manager",
]

# Extra Naukri-only stems. LinkedIn's guest API throttles hard, so the
# broader and noisier queries are not worth spending its budget on.
HIDDEN_QUERIES = [
    "process associate finance and accounting",
    "accounts executive",
    "revenue analyst",
    "cost accountant",
    "billing executive",
    "gst compliance",
]
INCLUDE_HIDDEN_SEARCHES = True

# LinkedIn gets a subset. 20 queries x 10 cities x 5 pages is 1,000 guest
# calls at 2 concurrent - it would spend the whole budget and still serve
# a fraction. Naukri is the primary source for Indian finance roles.
LINKEDIN_QUERIES = [
    "accounts receivable", "accounts payable", "general ledger",
    "financial analyst", "accountant", "internal audit", "finance manager",
]
LINKEDIN_CITIES = ["Bengaluru", "Chennai", "Hyderabad", "Mumbai", "Pune"]

# --- Interview-odds weights (relative) --------------------------------
# Relative, not out of 100 - interview_score() divides by their sum. There
# is deliberately no resume term: the score is published to strangers, so
# every part of it has to mean something to a stranger.
W_DIRECTNESS = 26   # named direct employer, not an agency mass-repost
W_FRESHNESS = 22   # posted hours/days ago = recruiter is actively screening
W_COMPETITION = 16   # few applicants = a CV actually gets read
W_AR_FIT = 16   # the keyword stems really did land on a finance role
W_EXPERIENCE = 8    # MY_EXPERIENCE_YEARS sits inside their band

# ======================= nothing to edit below =======================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

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
    "unapplied cash", "unallocated cash", "on-account", "customer statement",
    "lockbox", "sundry debtors", "debtors", "ar reconciliation",
]

# ---------------------------------------------------------------------
# Finance job families
# ---------------------------------------------------------------------
# The bot used to answer one question - "is this AR?" - and throw away
# everything else. The site serves other people now, so instead of a gate
# this is a CLASSIFIER: every finance role is kept and tagged, and the
# page turns these names into the filter chips a visitor picks from.
#
# Order matters. The list is walked top to bottom and the first title hit
# wins, so specific families sit above generic ones: an "AR Accountant"
# must land in Accounts Receivable, not in the catch-all Accounting.
#
# title: terms that, in the TITLE, settle the family on their own.
# body:  supporting terms, used only when no title term matched, and then
#        only if they are dense enough to clear MIN_FAMILY_PERCENT.
from finance_catalog import JOB_FAMILIES, SKILL_VOCAB, ADDITIONAL_QUERIES, SPECIALISMS, classify_finance

FAMILY_NAMES = [name for name, _, _ in JOB_FAMILIES]
PRIMARY_QUERIES = list(dict.fromkeys(PRIMARY_QUERIES + ADDITIONAL_QUERIES))
LINKEDIN_QUERIES = list(dict.fromkeys(LINKEDIN_QUERIES + [row[3] for row in SPECIALISMS]))

CONSULTANCY_WORDS = [
    "consultanc", "consultant", "consulting", "staffing", "recruit",
    "manpower", "placement", "hiring solutions", "hr services",
    "hr solutions", "talent solutions", "talent acquisition", "job hub",
    "hr consult", "advisory services", "outsourcing", "workforce",
    "resourcing", "search partners", "headhunt", "job placement",
    # High-precision additions only. "global services" and "associates" are
    # deliberately absent: WNS Global Services is a real employer, and
    # plenty of genuine firms are "<name> & Associates". Anything these
    # miss lands on the honest "unverified" badge rather than a wrong one.
    "executive search", "business solutions", "career solutions",
    "job solutions", "personnel", "recruitment services",
]

# Named staffing houses whose company name gives no other clue. These are
# intermediaries: applying reaches a recruiter's inbox, not the employer.
# BPO/GCC employers (Genpact, WNS, Accenture, Infosys...) deliberately are
# NOT here - they are the actual employer and hire AR staff directly.
KNOWN_STAFFING_FIRMS = [
    "quess", "randstad", "adecco", "teamlease", "manpowergroup",
    "kelly services", "hays", "michael page", "abc consultants", "antal",
    "gi group", "careernet", "ciel hr", "peoplestrong", "talentpro",
    "aarvi", "magna infotech", "ikya", "spectrum talent", "crescendo global",
    "black turtle", "vision india", "2coms", "talentahead", "huquo",
    "seven consultancy", "promaynov", "valenta",
]

# Titles from a different job family. HARD terms win even when the title
# also contains an AR word (an "O2C Functional Consultant" is an SAP
# implementation career, not an AR operations hire).
HARD_BLOCK_TITLE_TERMS = [
    "developer", "software engineer", "test engineer", "tester", "testing",
    "qa engineer", "architect", "devops", "data scientist", "full stack",
    "frontend", "backend", "sap consultant", "sap functional",
    "sap technical", "functional consultant", "technical consultant",
    "solution engineer", "solution architect", "implementation consultant",
    "erp consultant", "oracle consultant", "presales", "pre-sales",
    "sales manager", "business development", "recruiter",
    "talent acquisition", "hr executive", "sap rar", "sap trm", "sap fico",
    "application delivery", "application support",
]

# An ERP platform name + a technical role word = an ERP implementation job,
# not an AR operations job - even when the title says "Cash Applications"
# (e.g. "SAP S/4HANA Cash Applications Consultant" configures the module,
# it doesn't run the AR desk). Both halves must be present to block.
ERP_PLATFORM_TERMS = [
    "sap", "oracle", "workday", "peoplesoft", "netsuite", "dynamics 365",
    "erp", "s4 hana", "s/4hana", "fusion", "blackline", "highradius",
]
ERP_ROLE_TERMS = [
    "consultant", "engineer", "architect", "developer", "administrator",
    "admin", "implementation", "testing", "tester", "technical", "functional",
    "solution", "support analyst", "delivery",
]

# Titles next door to finance without being it. Kept only when the body
# reads overwhelmingly like one of the families.
#
# This list used to be four times longer and contained internal audit,
# tax, treasury, payroll and fp&a - all of which are now FAMILIES we
# deliberately collect. Blocking them here while classifying them there
# would silently delete five whole categories from the site.
SOFT_BLOCK_TITLE_TERMS = [
    "supply chain", "logistics", "procurement", "legal",
    "company secretary", "data analyst", "business analyst",
    "contracts specialist", "operations manager", "sales",
    "customer support", "customer service",
]

THIRD_PARTY_PHRASES = [
    "on behalf of our client", "on behalf of a client", "for our client",
    "one of our clients", "leading mnc client", "reputed client",
    "client of ours", "our client is hiring", "hiring for our client",
    "staffing partner", "recruitment partner", "manpower consultancy",
    "immediate requirement for our client", "confidential search",
]

LOCATION_ALIASES = {
    "chennai": {"chennai", "madras"},
    "bengaluru": {"bengaluru", "bangalore", "banglore", "bengalooru"},
    "bangalore": {"bengaluru", "bangalore", "banglore", "bengalooru"},
    "mumbai": {"mumbai", "bombay", "navi mumbai", "thane"},
    "hyderabad": {"hyderabad", "secunderabad"},
    "delhi": {"delhi", "new delhi", "ncr"},
    "gurgaon": {"gurgaon", "gurugram"},
    "noida": {"noida", "greater noida"},
    "pune": {"pune"},
    "kolkata": {"kolkata", "calcutta"},
    "coimbatore": {"coimbatore"},
}

APPLICANT_RE = re.compile(r"(\d[\d,]*)\s*applicant", re.I)
EARLY_RE = re.compile(r"first\s*\d+\s*applicant", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)")
CONTACT_IGNORE_DOMAINS = ("naukri.com", "linkedin.com", "indeed.com",
                          "noreply", "no-reply", "donotreply", "example.com")


def resolve(path):
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.join(SCRIPT_DIR, path)


def tokenize(text):
    if not text:
        return []
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.&]{1,}", text.lower())
    return [w for w in words if w not in STOPWORDS]


def clean(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ------------------------- salary -------------------------

def parse_salary_lpa(label):
    """
    Turn a portal salary string into (min_lpa, max_lpa), or None when the
    posting doesn't disclose one. Handles the shapes both portals emit:
      '10-15 Lacs PA' | 'Rs 12,00,000 - 18,00,000 PA' | '4-6 LPA'
      '50,000 - 70,000 a month' | 'Above 15 Lacs' | 'Not disclosed'
    """
    if not label:
        return None
    s = label.lower()
    s = s.replace(",", "").replace("₹", " ").replace("rs.", " ")
    s = s.replace("rs ", " ").replace("inr", " ")
    if "not disclosed" in s or "unspecified" in s or "as per" in s:
        return None

    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    nums = [n for n in nums if n > 0]
    if not nums:
        return None

    monthly = ("month" in s or "/mo" in s or " pm" in s or "p.m" in s)
    if "cr" in s or "crore" in s:
        scale = 100.0                     # crore -> LPA
    elif "lac" in s or "lakh" in s or "lpa" in s or "l pa" in s:
        scale = 1.0                       # already lakhs per annum
    elif max(nums) >= 100000:
        scale = 1.0 / 100000.0            # raw annual rupees
    elif monthly or max(nums) >= 10000:
        scale = 12.0 / 100000.0           # raw monthly rupees
    else:
        scale = 1.0                       # bare numbers: assume lakhs

    vals = sorted(n * scale for n in nums[:2])
    lo = vals[0]
    hi = vals[-1] if len(vals) > 1 else vals[0]

    if "above" in s or "more than" in s or "+" in label:
        hi = max(hi, 99.0)
    if "upto" in s or "up to" in s or "under" in s:
        lo = 0.0
    if hi <= 0:
        return None
    return (round(lo, 2), round(hi, 2))


def salary_verdict(job):
    """
    (keep, band_text, basis, penalty) - the hard money gate.
    basis is 'Stated' when the portal published a range, 'Est.' when we
    inferred plausibility from the experience band instead.
    """
    rng = parse_salary_lpa(job.get("salary", ""))
    lo_want = SALARY_MIN_LPA - SALARY_TOLERANCE_LPA
    hi_want = SALARY_MAX_LPA + SALARY_TOLERANCE_LPA

    if rng:
        lo, hi = rng
        band = f"{lo:g}-{hi:g}" if hi < 99 else f"{lo:g}+"
        in_band = (hi >= lo_want and lo <= hi_want)
        # Out of YOUR band is not out of the visitor's band. The row is
        # published either way with its real numbers attached; only the
        # owner's odds score takes the hit.
        return (in_band or not SALARY_IS_A_GATE), band, "Stated", (0 if in_band else 6)

    if not KEEP_UNDISCLOSED_SALARY:
        return (not SALARY_IS_A_GATE), "-", "Not disclosed", 0

    # No CTC published. Keep it only when the experience they ask for is
    # consistent with a 10-15 LPA AR hire - roughly 4+ years, not a fresher
    # req and not a 12+ year manager req you'd be under-levelled for.
    exp = parse_exp_range(job.get("experience", ""))
    if exp:
        lo_y, hi_y = exp
        # Junior and senior are relative to whoever is reading. Both are
        # kept and labelled; the page's experience filter sorts it out.
        if hi_y < 3:
            return (not SALARY_IS_A_GATE), "-", "Not disclosed (junior band)", 6
        if lo_y > MY_EXPERIENCE_YEARS + 4:
            return (not SALARY_IS_A_GATE), "-", "Not disclosed (senior band)", 6

    # A row that came back through Naukri's own ctcFilter=10to15 facet is
    # far better evidence than our guess: the employer set an in-band CTC,
    # they just didn't publish it. Penalise that only lightly.
    if job.get("_portal_ctc"):
        return True, "in band (not shown)", "Est. (portal band)", 3
    if exp:
        # We at least matched the experience band to your bracket.
        return True, "-", "Est. (from experience)", 8
    # No CTC and no experience anywhere - we genuinely cannot vouch for the
    # money. Keep it (it may still be a great AR req) but penalise it hard
    # and say so plainly in the sheet rather than implying it's in band.
    return True, "-", "Unknown - verify CTC", 12


# ------------------------- dates -------------------------

def days_from_epoch(ms):
    if not ms:
        return None
    try:
        posted = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - posted).days)
    except Exception:
        return None


def days_from_iso(dt):
    if not dt:
        return None
    try:
        d = datetime.fromisoformat(dt[:10]).replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - d).days)
    except Exception:
        return None


def days_from_label(label):
    if not label:
        return None
    s = label.lower()
    if "just" in s or "today" in s or "few" in s or "hour" in s or "now" in s:
        return 0
    if "yesterday" in s:
        return 1
    m = re.search(r"(\d+)\+?\s*day", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\+?\s*week", s)
    if m:
        return int(m.group(1)) * 7
    if "month" in s:
        m = re.search(r"(\d+)", s)
        return int(m.group(1)) * 30 if m else 30
    return None


def posting_date_str(days_old):
    if days_old is None:
        return "?"
    try:
        return (datetime.now() - timedelta(days=int(days_old))).strftime("%d-%b-%Y")
    except Exception:
        return "?"


def age_str(days_old):
    if days_old is None:
        return "?"
    if days_old <= 0:
        return "Today"
    if days_old == 1:
        return "Yesterday"
    return f"{days_old}d ago"


# ------------------------- misc extractors -------------------------

def extract_applicants(text):
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


def is_consultancy(company):
    c = (company or "").lower()
    return (any(w in c for w in CONSULTANCY_WORDS)
            or any(f in c for f in KNOWN_STAFFING_FIRMS))


def _family_body_strength(hay, body_terms):
    """How densely a job's text reads like one family. 5 distinct terms
    is treated as a full signal - beyond that the extra hits say nothing
    new, they just reward long job descriptions."""
    hits = sum(1 for t in body_terms if t in hay)
    return min(1.0, hits / 5.0)


def classify_family(job):
    """
    (family, basis, strength). basis is 'title' when the job title named
    the family outright, 'body' when only the description gave it away,
    and None when nothing in the row reads like finance at all.

    A title hit is never scored below 0.6: "Accounts Payable Executive"
    on a two-line Naukri stub IS an AP job, and letting a thin description
    drag its strength down would push it under the gate for no good reason.
    """
    return classify_finance(job)


def finance_gate(job):
    """
    Is this a finance job at all? Returns (keep, family, strength, reason).

    This replaced ar_gate(). The old one asked "is this AR?" and deleted
    1,003 of 1,323 rows on a typical run - correct when the only reader
    was one AR specialist, wrong now that the page serves anyone. What
    survives as a gate is only the stuff that is genuinely not a finance
    job: engineering titles, ERP implementation work, and roles from
    neighbouring departments whose text never reads like finance.
    """
    title = (job.get("title") or "").lower()

    family, basis, strength = classify_family(job)
    # Finance systems and finance sales are intentional career tracks.
    # Only explicit specialist title matches can bypass adjacent-role gates.
    if basis == "title" and family in {
        "Finance Systems & Transformation", "Insurance", "Banking & Lending",
        "Asset & Wealth Management", "Quantitative Finance", "Investment Operations",
    } and not any(t in title for t in ("recruiter", "talent acquisition", "software engineer", "developer")):
        return True, family, strength, f"{family} by specialist title"

    for term in HARD_BLOCK_TITLE_TERMS:
        if term in title:
            return False, None, 0.0, f"different job family ({term.strip()})"

    erp = next((t for t in ERP_PLATFORM_TERMS
                if re.search(r"(?<![a-z])" + re.escape(t), title)), None)
    if erp and any(t in title for t in ERP_ROLE_TERMS):
        return False, None, 0.0, f"ERP implementation role ({erp.upper()})"

    pct = strength * 100

    for term in SOFT_BLOCK_TITLE_TERMS:
        if term in title:
            if family and pct >= 75:
                return True, family, strength, f"adjacent title, but the JD is {family}"
            return False, None, 0.0, f"adjacent department ({term.strip()})"

    if family is None:
        return False, None, 0.0, "no finance signal"
    if basis == "title":
        return True, family, strength, f"{family} by title"
    if pct >= MIN_FAMILY_PERCENT:
        return True, family, strength, f"{family} - JD says so"
    return False, None, 0.0, f"only {round(pct)}% finance signal"


def is_confidential_company(company):
    c = (company or "").strip().lower()
    return c in ("", "confidential", "confidential company", "company confidential",
                 "a reputed company", "reputed company", "leading company",
                 "reputed organisation", "reputed organization", "client of")


def detect_employment_type(*texts):
    hay = " ".join(t for t in texts if t).lower()
    if not hay.strip():
        return "Full-time (assumed)"
    if re.search(r"\b(intern|internship|internships)\b", hay):
        return "Internship"
    if "part-time" in hay or "part time" in hay:
        return "Part-time"
    if any(k in hay for k in ("contract", "temporary", "fixed term",
                              "fixed-term", "c2h")):
        return "Contract"
    if any(k in hay for k in ("full-time", "full time", "permanent")):
        return "Full-time"
    return "Full-time (assumed)"


def mine_experience(text):
    """
    Pull a years-of-experience band out of JD prose. LinkedIn cards carry no
    experience field at all, so without this every LinkedIn row sails
    through the salary gate on zero evidence. Returns a label like '5-8 Yrs'.
    """
    if not text:
        return ""
    t = text.lower().replace("–", "-").replace("—", "-")
    patterns = [
        r"(\d{1,2})\s*(?:to|-|~)\s*(\d{1,2})\+?\s*(?:\+)?\s*(?:years?|yrs?)",
        r"(?:minimum|min\.?|at least|atleast)\s*(?:of\s*)?(\d{1,2})\+?\s*(?:years?|yrs?)",
        r"(\d{1,2})\s*\+\s*(?:years?|yrs?)",
        r"(\d{1,2})\s*(?:years?|yrs?)\s*(?:of\s*)?(?:relevant\s*|work\s*|prior\s*)?experience",
    ]
    for i, pat in enumerate(patterns):
        m = re.search(pat, t)
        if not m:
            continue
        groups = [int(g) for g in m.groups() if g]
        if not groups or groups[0] > 30:
            continue
        if len(groups) == 2 and groups[1] <= 30 and groups[1] >= groups[0]:
            return f"{groups[0]}-{groups[1]} Yrs"
        return (f"{groups[0]}+ Yrs" if i in (1, 2)
                else f"{groups[0]}-{groups[0]} Yrs")
    return ""


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


# ------------------------- browser plumbing -------------------------

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
                return (ctx.pages[0] if ctx.pages else ctx.new_page()), ctx
            except Exception:
                continue
        ctx = p.chromium.launch_persistent_context(
            data_dir, headless=headless, **common)
        return (ctx.pages[0] if ctx.pages else ctx.new_page()), ctx

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
    return ctx.new_page(), browser


# A bounded-concurrency fetch pool that runs INSIDE the page. This is the
# whole performance story: N requests in flight at once, same-origin, with
# the session's cookies, and zero page navigation or rendering per request.
JS_FETCH_JSON = r"""
async (payload) => {
  const {urls, headers, conc, timeoutMs} = payload;
  const out = new Array(urls.length).fill(null);
  let cursor = 0;
  async function one(i) {
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), timeoutMs);
    try {
      const r = await fetch(urls[i], {
        headers: headers, credentials: 'include', signal: ac.signal
      });
      if (r.ok) out[i] = await r.json();
      else out[i] = {__status: r.status};
    } catch (e) {
      out[i] = {__error: String(e)};
    } finally {
      clearTimeout(t);
    }
  }
  async function worker() {
    while (cursor < urls.length) { await one(cursor++); }
  }
  await Promise.all(
    Array.from({length: Math.min(conc, urls.length)}, worker));
  return out;
}
"""

# LinkedIn's guest endpoints return HTML fragments. Parsing them in the
# browser (DOMParser) instead of shipping raw HTML back to Python keeps the
# payload small and avoids a regex-scraping dependency.
JS_FETCH_LINKEDIN_CARDS = r"""
async (payload) => {
  const {urls, conc, timeoutMs} = payload;
  const out = new Array(urls.length).fill(null);
  let cursor = 0;
  const T = (el, sel) => {
    const n = el.querySelector(sel);
    return n ? n.textContent.trim().replace(/\s+/g, ' ') : '';
  };
  async function one(i) {
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), timeoutMs);
    try {
      const r = await fetch(urls[i], {credentials: 'include', signal: ac.signal});
      if (!r.ok) { out[i] = {status: r.status, jobs: []}; return; }
      const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
      const cards = doc.querySelectorAll('div.base-card, div.base-search-card');
      const jobs = [];
      cards.forEach(c => {
        const title = T(c, '.base-search-card__title');
        if (!title) return;
        const link = c.querySelector('a.base-card__full-link') ||
                     c.querySelector('a');
        const timeEl = c.querySelector('time');
        jobs.push({
          title: title,
          company: T(c, '.base-search-card__subtitle'),
          location: T(c, '.job-search-card__location'),
          salary: T(c, '.job-search-card__salary-info'),
          benefits: T(c, '.result-benefits__text'),
          posted: timeEl ? (timeEl.getAttribute('datetime') || '') : '',
          postedLabel: T(c, '.job-search-card__listdate') ||
                       T(c, '.job-search-card__listdate--new'),
          urn: c.getAttribute('data-entity-urn') || '',
          url: link ? (link.getAttribute('href') || '').split('?')[0] : '',
          cardText: (c.textContent || '').replace(/\s+/g, ' ').trim()
        });
      });
      out[i] = {status: 200, jobs: jobs};
    } catch (e) {
      out[i] = {status: 0, jobs: [], error: String(e)};
    } finally { clearTimeout(t); }
  }
  async function worker() { while (cursor < urls.length) { await one(cursor++); } }
  await Promise.all(Array.from({length: Math.min(conc, urls.length)}, worker));
  return out;
}
"""

JS_FETCH_LINKEDIN_DETAIL = r"""
async (payload) => {
  const {urls, conc, timeoutMs} = payload;
  const out = new Array(urls.length).fill(null);
  let cursor = 0;
  const T = (el, sel) => {
    const n = el.querySelector(sel);
    return n ? n.textContent.trim().replace(/\s+/g, ' ') : '';
  };
  async function one(i) {
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), timeoutMs);
    try {
      const r = await fetch(urls[i], {credentials: 'include', signal: ac.signal});
      if (!r.ok) { out[i] = null; return; }
      const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
      let etype = '', seniority = '';
      doc.querySelectorAll('li.description__job-criteria-item').forEach(li => {
        const k = T(li, '.description__job-criteria-subheader').toLowerCase();
        const v = T(li, '.description__job-criteria-text');
        if (k.indexOf('employment type') >= 0) etype = v;
        if (k.indexOf('seniority') >= 0) seniority = v;
      });
      out[i] = {
        applicantsText: T(doc, '.num-applicants__caption') ||
                        T(doc, 'figure.num-applicants__figure') ||
                        T(doc, '.jobs-unified-top-card__applicant-count'),
        description: T(doc, '.show-more-less-html__markup') ||
                     T(doc, '.description__text'),
        employmentType: etype,
        seniority: seniority,
        salary: T(doc, '.compensation__salary') || T(doc, '.salary')
      };
    } catch (e) {
      out[i] = null;
    } finally { clearTimeout(t); }
  }
  async function worker() { while (cursor < urls.length) { await one(cursor++); } }
  await Promise.all(Array.from({length: Math.min(conc, urls.length)}, worker));
  return out;
}
"""


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# ------------------------- Naukri -------------------------

# Naukri's search API rejects anonymous calls with {"message":"recaptcha
# required"} unless the request carries the same anti-bot headers its own
# page JS attaches - notably a per-session `nkparam` token. We can't forge
# that, so we let the real site mint one: navigate ONCE to a search page,
# intercept the XHR it fires, and reuse those headers for every subsequent
# parallel API call. One navigation buys the whole run.
NAUKRI_BASE_HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "appid": "109",
    "systemid": "Naukri",
    "clientid": "d3skt0p",
}


def naukri_seo_key(keyword, location):
    kw = slugify(keyword)
    return f"{kw}-jobs-in-{slugify(location)}" if location else f"{kw}-jobs"


def naukri_srp_url(keyword, location):
    return "https://www.naukri.com/" + naukri_seo_key(keyword, location)


def warm_naukri(page, keyword, location):
    """
    Visit one real SRP and capture the anti-bot headers Naukri's own JS uses.
    Returns a headers dict, or None if the token never showed up (in which
    case the caller falls back to scraping the rendered page).
    """
    captured = {}

    def on_request(req):
        if "jobapi/v3/search" in req.url and "nkparam" in req.headers:
            captured.update(req.headers)

    page.on("request", on_request)
    try:
        page.goto(naukri_srp_url(keyword, location),
                  wait_until="domcontentloaded", timeout=60000)
        # The search XHR fires shortly after DOM ready; give it room.
        for _ in range(20):
            if captured:
                break
            page.wait_for_timeout(500)
    except Exception as e:
        print(f"  ! Naukri warm-up navigation failed: {e}")
    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass

    if not captured:
        return None
    headers = dict(NAUKRI_BASE_HEADERS)
    for key in ("appid", "systemid", "clientid", "gid", "nkparam"):
        if key in captured:
            headers[key] = captured[key]
    return headers


def naukri_api_url(keyword, location, page_no):
    """
    Naukri's own search API. Two things make this fast: noOfResults=100 (its
    max, so 3 calls replace 15 page-loads), and pushing the salary/recency/
    experience cuts SERVER-side via the same facets the website uses -
    ctcFilter in particular returns a set where every disclosed CTC lands in
    band, instead of us downloading 2,600 rows to throw 2,500 away.
    """
    parts = [
        f"noOfResults={NAUKRI_RESULTS_PER_PAGE}",
        "urlType=search_by_key_loc" if location else "urlType=search_by_keyword",
        "searchType=adv",
        f"keyword={quote(keyword)}",
        f"pageNo={page_no}",
        "sort=f",                                   # freshest first
        f"seoKey={naukri_seo_key(keyword, location)}",
        "src=directSearch",
    ]
    if location:
        parts.append(f"location={quote(location.lower())}")
    if NAUKRI_USE_PORTAL_FILTERS:
        # Freshness is pushed server-side for everyone - a stale job helps
        # nobody. Salary and experience are NOT, unless this run is scoped
        # to the owner: those facets would make Naukri filter the whole
        # site down to one person's bracket before we ever see the rows.
        parts.append(f"jobAge={min(MAX_DAYS_OLD, 15)}")
        if SALARY_IS_A_GATE:
            parts += [
                f"ctcFilter={int(SALARY_MIN_LPA)}to{int(SALARY_MAX_LPA)}",
                f"experience={MY_EXPERIENCE_YEARS}",
            ]
    return "https://www.naukri.com/jobapi/v3/search?" + "&".join(parts)


def normalize_naukri(job):
    jd = job.get("jdURL", "") or ""
    url = ("https://www.naukri.com" + jd) if jd.startswith("/") else jd
    placeholders = {p.get("type"): p.get("label", "")
                    for p in (job.get("placeholders") or [])}
    days = days_from_epoch(job.get("createdDate"))
    if days is None:
        days = days_from_label(job.get("footerPlaceholderLabel", ""))

    applicants = None
    for key in ("applyCount", "numApplicants", "applicantCount"):
        v = job.get(key)
        if isinstance(v, (int, float)):
            applicants = int(v)
            break

    desc = clean(job.get("jobDescription", ""))
    title = clean(job.get("title", ""))
    ab = job.get("ambitionBoxData") or {}
    return {
        "source": "Naukri",
        "job_id": "N:" + str(job.get("jobId", "") or url or title),
        "naukri_id": str(job.get("jobId", "") or ""),
        "title": title,
        "company": clean(job.get("companyName", "")),
        "employment_type": job.get("employmentType") or
                           detect_employment_type(title, desc),
        "experience": placeholders.get("experience", ""),
        "salary": placeholders.get("salary", ""),
        "location": placeholders.get("location", ""),
        "skills": clean((job.get("tagsAndSkills") or "").replace(",", ", ")),
        "description": desc,
        "days_old": days,
        "reviews": ab.get("ReviewsCount") or "",
        "applicants": applicants,
        "url": url,
    }


def _naukri_call(page, urls, headers):
    """One concurrent burst of API calls -> list of parsed JSON (or None)."""
    out = []
    for batch in chunked(urls, NAUKRI_CONCURRENCY * 4):
        out.extend(page.evaluate(JS_FETCH_JSON, {
            "urls": batch, "headers": headers,
            "conc": NAUKRI_CONCURRENCY, "timeoutMs": 25000,
        }))
    return out


def _harvest(results, jobs, seen):
    added, bad = 0, 0
    for res in results:
        if not isinstance(res, dict) or "jobDetails" not in res:
            bad += 1
            continue
        for raw in (res.get("jobDetails") or []):
            try:
                job = normalize_naukri(raw)
            except Exception:
                continue
            if job["job_id"] in seen:
                continue
            seen.add(job["job_id"])
            # Only claim "portal said it's in band" when the CTC facet was
            # actually sent - otherwise it is an unearned vote of confidence.
            job["_portal_ctc"] = NAUKRI_USE_PORTAL_FILTERS and SALARY_IS_A_GATE
            jobs.append(job)
            added += 1
    return added, bad


def fetch_naukri(page, queries, headers):
    """
    Two-phase paging. Phase 1 asks for page 1 of every query x city, which
    also tells us noOfJobs for each. Phase 2 then requests ONLY the extra
    pages that actually exist - asking for page 3 of a 111-result query
    returns HTTP 400 and wastes a round trip.
    """
    combos = [(q, loc) for q in queries for loc in LOCATIONS]
    jobs, seen, t0 = [], set(), time.time()

    p1_urls = [naukri_api_url(q, loc, 1) for q, loc in combos]
    print(f"[Naukri]   phase 1: {len(p1_urls)} calls "
          f"({NAUKRI_CONCURRENCY} at a time)...")
    p1 = _naukri_call(page, p1_urls, headers)
    added, bad = _harvest(p1, jobs, seen)
    print(f"    -> {added} rows ({time.time() - t0:.0f}s)"
          + (f", {bad} calls returned no data" if bad else ""))

    p2_urls = []
    for (q, loc), res in zip(combos, p1):
        if not isinstance(res, dict):
            continue
        total = res.get("noOfJobs") or 0
        have = len(res.get("jobDetails") or [])
        if not have:
            continue
        pages = min(NAUKRI_PAGES,
                    -(-int(total) // NAUKRI_RESULTS_PER_PAGE))  # ceil div
        for pg in range(2, pages + 1):
            p2_urls.append(naukri_api_url(q, loc, pg))

    if p2_urls:
        print(f"[Naukri]   phase 2: {len(p2_urls)} deeper-page calls...")
        added2, bad2 = _harvest(_naukri_call(page, p2_urls, headers), jobs, seen)
        print(f"    -> {added2} more rows ({time.time() - t0:.0f}s)"
              + (f", {bad2} empty" if bad2 else ""))

    print(f"[Naukri]   {len(jobs)} unique rows in {time.time() - t0:.0f}s")
    return jobs


def fetch_naukri_dom(page, queries):
    """
    Fallback for when the anti-bot token can't be captured: scrape the
    rendered search pages. Much slower (a real navigation per page), so it
    only covers page 1 of each query x city.
    """
    print("[Naukri]   ! API token unavailable - falling back to page scraping.")
    jobs, seen = [], set()
    for q in queries:
        for loc in LOCATIONS:
            try:
                page.goto(naukri_srp_url(q, loc),
                          wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
                cards = page.query_selector_all(
                    "div.srp-jobtuple-wrapper, article.jobTuple")
                for c in cards:
                    job = normalize_naukri_card(c)
                    if job and job["job_id"] not in seen:
                        seen.add(job["job_id"])
                        job["_portal_ctc"] = False
                        jobs.append(job)
            except Exception as e:
                print(f"    ({q} / {loc}: {e})")
            print(f"    {q} in {loc}: {len(jobs)} rows so far")
    return jobs


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
    href = attr("a.title", "href") or ""
    skills = ", ".join(s.inner_text().strip()
                       for s in card.query_selector_all("ul.tags-gt li"))
    desc = clean(txt("span.job-desc"))
    m = re.search(r"-(\d{6,})\?", href) or re.search(r"(\d{9,})", href)
    return {
        "source": "Naukri",
        "job_id": "N:" + (href or title),
        "naukri_id": m.group(1) if m else "",
        "title": clean(title),
        "company": clean(txt("a.comp-name") or txt("a.subTitle")),
        "employment_type": detect_employment_type(title, desc, skills),
        "experience": clean(txt("span.expwdth") or txt(".exp")),
        "salary": clean(txt("span.sal") or txt(".sal-wrap span")),
        "location": clean(txt("span.locWdth") or txt(".loc")),
        "skills": clean(skills),
        "description": desc,
        "days_old": days_from_label(txt("span.job-post-day")),
        "reviews": "",
        "applicants": None,
        "url": href,
    }


def naukri_detail_urls(ids):
    return [f"https://www.naukri.com/jobapi/v4/job/{i}?src=jobsearchDesk"
            for i in ids]


def enrich_naukri(page, jobs, headers):
    """Full JD for shortlist candidates: better AR %, skills and contacts."""
    if not headers:
        return
    targets = [j for j in jobs if j["source"] == "Naukri" and j.get("naukri_id")]
    if not targets:
        return
    targets = targets[:ENRICH_LIMIT]
    print(f"[Naukri]   full JD for {len(targets)} candidates...")
    ids = [j["naukri_id"] for j in targets]
    idx = 0
    for batch in chunked(naukri_detail_urls(ids), DETAIL_CONCURRENCY * 4):
        results = page.evaluate(JS_FETCH_JSON, {
            "urls": batch, "headers": headers,
            "conc": DETAIL_CONCURRENCY, "timeoutMs": 25000,
        })
        for res in results:
            job = targets[idx]
            idx += 1
            if not isinstance(res, dict):
                continue
            details = res.get("jobDetails") or res.get("jobdetails") or {}
            if not isinstance(details, dict):
                continue
            desc = clean(details.get("description", ""))
            if len(desc) > len(job["description"]):
                job["description"] = desc
            if not job["salary"]:
                sd = details.get("salaryDetail") or {}
                job["salary"] = clean(sd.get("label", "")) or job["salary"]
            job["_full_jd"] = True


# ------------------------- LinkedIn -------------------------

def linkedin_search_url(keyword, location, start):
    loc = location or "India"
    tpr = f"r{MAX_DAYS_OLD * 86400}"   # seconds window - LinkedIn's own filter
    return ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/"
            f"search?keywords={quote(keyword)}&location={quote(loc)}"
            f"&f_TPR={tpr}&sortBy=DD&start={start}")


def linkedin_detail_url(job_id):
    return f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"


def linkedin_job_id(card):
    m = re.search(r"jobPosting:(\d+)", card.get("urn", "") or "")
    if m:
        return m.group(1)
    m = re.search(r"(\d{8,})", card.get("url", "") or "")
    return m.group(1) if m else ""


def normalize_linkedin(card):
    title = clean(card.get("title", ""))
    if not title:
        return None
    jid = linkedin_job_id(card)
    days = days_from_iso(card.get("posted", ""))
    if days is None:
        days = days_from_label(card.get("postedLabel", ""))
    return {
        "source": "LinkedIn",
        "job_id": "L:" + (jid or card.get("url", "") or title),
        "linkedin_id": jid,
        "title": title,
        "company": clean(card.get("company", "")),
        "employment_type": "",
        "experience": "",
        "salary": clean(card.get("salary", "")),
        "location": clean(card.get("location", "")),
        "skills": "",
        "description": "",
        "days_old": days,
        "reviews": "",
        "applicants": extract_applicants(card.get("cardText", "")),
        "url": card.get("url", ""),
    }


def linkedin_search_urls(queries):
    # Cover every career area before spending the time budget on deeper
    # pages of the first few AR searches.
    urls = []
    for pg in range(LINKEDIN_PAGES):
        for loc in LINKEDIN_CITIES:
            for q in queries:
                urls.append(linkedin_search_url(q, loc, pg * LINKEDIN_PAGE_SIZE))
    return urls


def fetch_linkedin(page, queries):
    urls = linkedin_search_urls(queries)

    print(f"[LinkedIn] {len(urls)} guest-API calls, "
          f"{LINKEDIN_CONCURRENCY} at a time...")
    jobs, seen, t0 = [], set(), time.time()
    pending = list(urls)

    # LinkedIn 429s hard. Rather than lose those pages, collect the rejected
    # URLs and re-run them after an increasing pause.
    out_of_time = False
    for attempt in range(LINKEDIN_RETRIES + 1):
        if not pending or out_of_time:
            break
        if attempt:
            wait = 5 * attempt
            print(f"    retry {attempt}: {len(pending)} throttled calls "
                  f"after {wait}s backoff...")
            time.sleep(wait)
        retry, done = [], 0
        for batch in chunked(pending, LINKEDIN_CONCURRENCY * 3):
            if time.time() - t0 > LINKEDIN_SEARCH_BUDGET_S:
                retry.extend(batch)
                out_of_time = True
                continue
            results = page.evaluate(JS_FETCH_LINKEDIN_CARDS, {
                "urls": batch, "conc": LINKEDIN_CONCURRENCY, "timeoutMs": 25000,
            })
            for url, res in zip(batch, results):
                done += 1
                if not isinstance(res, dict) or res.get("status") in (429, 0):
                    retry.append(url)
                    continue
                for card in (res.get("jobs") or []):
                    j = normalize_linkedin(card)
                    if j and j["job_id"] not in seen:
                        seen.add(j["job_id"])
                        jobs.append(j)
            print(f"    {done}/{len(pending)} calls -> {len(jobs)} rows "
                  f"({time.time() - t0:.0f}s)")
            time.sleep(LINKEDIN_PACE_SECONDS)   # pacing beats getting blocked
        pending = retry

    if out_of_time:
        print(f"    (stopped at the {LINKEDIN_SEARCH_BUDGET_S}s LinkedIn "
              f"budget with {len(pending)} calls unserved)")
    elif pending:
        print(f"    ({len(pending)} calls stayed rate-limited - "
              f"Naukri coverage carries this run)")
    return jobs


def enrich_linkedin(page, jobs):
    """LinkedIn cards carry almost nothing - the detail fetch is what makes
    a LinkedIn row scoreable (applicants, JD, employment type, salary)."""
    targets = [j for j in jobs
               if j["source"] == "LinkedIn" and j.get("linkedin_id")]
    if not targets:
        return
    targets = targets[:LINKEDIN_ENRICH_LIMIT]
    print(f"[LinkedIn] detail pages for {len(targets)} candidates "
          f"(applicant counts live here)...")

    def apply_detail(job, res):
        if not isinstance(res, dict):
            return False
        n = extract_applicants(res.get("applicantsText", ""))
        if n is not None:
            job["applicants"] = n
        desc = clean(res.get("description", ""))
        if len(desc) > len(job["description"]):
            job["description"] = desc
        job["employment_type"] = (res.get("employmentType") or
                                  job["employment_type"] or
                                  detect_employment_type(job["title"], desc))
        if res.get("salary") and not job["salary"]:
            job["salary"] = clean(res["salary"])
        if res.get("seniority"):
            job["_seniority"] = res["seniority"]
        # LinkedIn has no experience field - recover it from the JD prose so
        # the salary/experience gates have something real to work with.
        if not job.get("experience"):
            job["experience"] = mine_experience(job["description"])
        job["_full_jd"] = True
        return True

    pending, t0, out_of_time = list(targets), time.time(), False
    for attempt in range(LINKEDIN_RETRIES + 1):
        if not pending or out_of_time:
            break
        if attempt:
            wait = 5 * attempt
            print(f"    retry {attempt}: {len(pending)} throttled "
                  f"after {wait}s...")
            time.sleep(wait)
        retry, done = [], 0
        for batch in chunked(pending, LINKEDIN_DETAIL_CONCURRENCY * 3):
            if time.time() - t0 > LINKEDIN_DETAIL_BUDGET_S:
                out_of_time = True
                break
            results = page.evaluate(JS_FETCH_LINKEDIN_DETAIL, {
                "urls": [linkedin_detail_url(j["linkedin_id"]) for j in batch],
                "conc": LINKEDIN_DETAIL_CONCURRENCY, "timeoutMs": 25000,
            })
            for job, res in zip(batch, results):
                done += 1
                if not apply_detail(job, res):
                    retry.append(job)
            time.sleep(LINKEDIN_PACE_SECONDS)
        got = sum(1 for j in targets if j.get("applicants") is not None)
        print(f"    {done} fetched, {got}/{len(targets)} have applicant counts"
              + (f" (hit the {LINKEDIN_DETAIL_BUDGET_S}s budget)"
                 if out_of_time else ""))
        pending = retry


# ------------------------- scoring -------------------------

def score_family(job):
    """How strongly the job reads like the family it was classified into.
    finance_gate() already did the work and cached it on the row; this is
    just the odds model reading that number back."""
    return float(job.get("_family_strength") or 0.0)


def score_freshness(job):
    d = job["days_old"]
    if d is None:
        return 0.35
    if d <= 0:
        return 1.0
    if d == 1:
        return 0.9
    if d <= 3:
        return 0.7
    if d <= 5:
        return 0.45
    if d <= MAX_DAYS_OLD:
        return 0.25
    return 0.0


def score_competition(job):
    n = job.get("applicants")
    if n is None:
        d = job["days_old"]
        if d is None:
            return 0.4
        return 0.8 if d <= 1 else 0.4
    if n <= 0:
        return 1.0
    if n <= 10:
        return 0.9
    if n <= 25:
        return 0.75
    if n <= 50:
        return 0.5
    if n <= 100:
        return 0.28
    return 0.08


def score_directness(job):
    """
    How close the posting is to the person who actually schedules interviews.
    A named employer posting its own req converts far better than the same JD
    blasted by five agencies under five different names.
    """
    consult = is_consultancy(job["company"])
    confidential = is_confidential_company(job["company"])
    third_party = reads_third_party(job.get("description", ""))
    dupes = job.get("_dup_company_count", 1)

    s = 0.35
    if not consult and not confidential and not third_party:
        s = 1.0
    elif consult and not third_party and not confidential:
        s = 0.55            # a named agency actively working a live req
    if confidential:
        s = min(s, 0.2)
    if third_party:
        s = min(s, 0.35)
    if dupes >= 3:
        s = min(s, 0.15)    # same JD under N company names = mass repost
    try:
        if float(str(job["reviews"]).replace(",", "")) > 50:
            s = min(1.0, s + 0.1)   # a real, rated employer
    except Exception:
        pass
    return s


def score_experience(job):
    rng = parse_exp_range(job.get("experience", ""))
    if rng is None:
        return 0.6
    lo, hi = rng
    if lo <= MY_EXPERIENCE_YEARS <= hi:
        return 1.0
    if lo - 1 <= MY_EXPERIENCE_YEARS <= hi + 2:
        return 0.6          # close enough that recruiters still shortlist
    return 0.15


def asked_for_skills(job):
    """
    Every SKILL_VOCAB term the posting actually names.

    This used to subtract the owner's resume and return the remainder,
    which made the published column a private diff nobody else could
    read. It is now just the posting's own ask - the page subtracts each
    visitor's uploaded resume from it in their browser, so the same
    string serves everyone.
    """
    hay = " ".join([job["title"], job["skills"], job["description"]]).lower()
    if not hay.strip():
        return "-"
    asked = []
    for term in SKILL_VOCAB:
        pattern = r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, hay) and term not in asked:
            asked.append(term)
    return ", ".join(asked) if asked else "-"


def interview_score(job, salary_penalty):
    ar = score_family(job)
    fresh = score_freshness(job)
    comp = score_competition(job)
    direct = score_directness(job)
    exp = score_experience(job)

    total = (direct * W_DIRECTNESS + fresh * W_FRESHNESS +
             comp * W_COMPETITION + ar * W_AR_FIT + exp * W_EXPERIENCE)
    weight_sum = (W_DIRECTNESS + W_FRESHNESS + W_COMPETITION +
                  W_AR_FIT + W_EXPERIENCE)
    score = max(0, round(total / weight_sum * 100) - salary_penalty)

    reasons = []
    if direct >= 0.9:
        reasons.append("direct employer req")
    elif direct >= 0.5:
        reasons.append("named agency, live req")
    else:
        reasons.append("intermediary/unnamed")
    if fresh >= 0.9:
        reasons.append("posted today/yesterday")
    elif fresh >= 0.7:
        reasons.append("posted <=3 days")
    n = job.get("applicants")
    if n == 0:
        reasons.append("be an early applicant")
    elif n is not None and n <= 25:
        reasons.append(f"only {n} applicants")
    elif n is not None:
        reasons.append(f"{n} applicants")
    fam = job.get("_family") or "finance"
    if ar >= 0.7:
        reasons.append(f"core {fam} role")
    elif ar >= 0.45:
        reasons.append(f"{fam}-leaning role")
    if exp >= 1.0:
        reasons.append("exp band matches exactly")

    return {
        "score": score,
        "verdict": verdict_for(score),
        "family": job.get("_family") or "Finance",
        "ar": round(ar * 100),
        "fresh": round(fresh * 100),
        "comp": round(comp * 100),
        "direct": round(direct * 100),
        "exp_fit": round(exp * 100),
        "why": "; ".join(reasons),
        "missing": asked_for_skills(job),
        "posted_date": posting_date_str(job["days_old"]),
        "age": age_str(job["days_old"]),
        "contact": extract_contact(job.get("description", "")),
    }


def verdict_for(score):
    if score >= 78:
        return "Apply today"
    if score >= 70:
        return "Strong - apply"
    return "Worth applying"


# ------------------------- filters -------------------------

def wanted_location_tokens():
    tokens = set()
    for loc in LOCATIONS:
        key = (loc or "").strip().lower()
        if key:
            tokens |= LOCATION_ALIASES.get(key, {key})
    return tokens


def passes_location(job, wanted):
    if not LOCATION_STRICT or not wanted:
        return True
    loc = (job.get("location") or "").lower()
    if not loc:
        return False
    if "remote" in loc or "india" == loc.strip():
        return True
    return any(tok in loc for tok in wanted)


def dedupe_key(job):
    """Same req reached through 5 different queries is still one job."""
    return (re.sub(r"[^a-z0-9]", "", job["title"].lower())[:60],
            re.sub(r"[^a-z0-9]", "", job["company"].lower())[:40],
            re.sub(r"[^a-z0-9]", "", (job["location"] or "").lower())[:20])


def jd_signature(job):
    sig = re.sub(r"\s+", " ", (job.get("description") or "")[:300].lower()).strip()
    return sig if len(sig) >= 60 else None


# ------------------------- output -------------------------

HEADERS = ["#", "Odds", "Verdict", "Portal", "Role", "Company", "Posted By",
           "Posted", "Age", "Location", "Experience", "Salary (LPA)",
           "Salary Basis", "Applicants", "Type", "Fit %",
           "Why it can convert", "Skills the Posting Asks For",
           "Direct Contact", "Apply Link", "Family"]

WIDTHS = [5, 7, 15, 9, 34, 26, 11, 12, 11, 16, 12, 13, 20, 11, 16, 7,
          46, 40, 24, 10, 22]


def _fill(ws, row, col, color):
    ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor=color)


def write_sheet(ws, jobs, title):
    ws.title = title
    ws.append(HEADERS)
    for col in range(1, len(HEADERS) + 1):
        c = ws.cell(row=1, column=col)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"

    for i, job in enumerate(jobs, 1):
        n = job.get("applicants")
        appl = "?" if n is None else ("Early" if n <= 0 else n)
        posted_by = "Recruiter" if is_consultancy(job["company"]) else "Direct"
        ws.append([
            i,
            job["score"],
            verdict_for(job["score"]),
            job["source"],
            job["title"],
            job["company"] or "(not named)",
            posted_by,
            job["posted_date"],
            job["age"],
            job["location"],
            job["experience"] or "-",
            job["salary_band"],
            job["salary_basis"],
            appl,
            job.get("employment_type") or "-",
            job["ar"],
            job["why"],
            job["missing"],
            job["contact"],
            job["url"],
            job.get("family", "Finance"),
        ])
        r = ws.max_row

        s = job["score"]
        _fill(ws, r, 2, "C6EFCE" if s >= 75 else "FFEB9C" if s >= 62 else "FFC7CE")
        _fill(ws, r, 7, "E2EFDA" if posted_by == "Direct" else "FFF2CC")
        if job["salary_basis"] == "Stated":
            _fill(ws, r, 13, "C6EFCE")
        elif job["salary_basis"].startswith("Est"):
            _fill(ws, r, 13, "FFF2CC")
        if n is not None:
            _fill(ws, r, 14, "C6EFCE" if n <= 25 else
                  "FFEB9C" if n <= 100 else "FFC7CE")
        a = job["ar"]
        _fill(ws, r, 16, "C6EFCE" if a >= 60 else "FFEB9C" if a >= 40 else "FFC7CE")
        if job["contact"] != "-":
            _fill(ws, r, 19, "C6EFCE")

        link = ws.cell(row=r, column=20)
        if job["url"]:
            link.hyperlink = job["url"]
            link.value = "Open"
            link.font = Font(color="0563C1", underline="single")

    for idx, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[get_column_letter(idx)].width = w
    for col in (18, 19):
        for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")


def write_summary(ws, stats, shortlist):
    ws.title = "Run Summary"
    ws.append(["Job Bot - finance roles", ""])
    ws.append(["Run at", datetime.now().strftime("%d-%b-%Y %H:%M")])
    ws.append(["Salary band preferred",
               f"{SALARY_MIN_LPA:g} - {SALARY_MAX_LPA:g} LPA "
               + ("(hard gate)" if SALARY_IS_A_GATE
                  else "(scoring only - nothing is dropped for salary)")])
    ws.append(["Cities", ", ".join(LOCATIONS)])
    ws.append(["Freshness window", f"last {MAX_DAYS_OLD} days"])
    ws.append(["Shortlist bar", f"interview-odds >= {INTERVIEW_MIN_SCORE}"])
    ws.append(["", ""])
    ws.append(["Stage", "Jobs"])
    for label, value in stats:
        ws.append([label, value])
    ws.append(["", ""])
    ws.append(["Shortlisted", len(shortlist)])
    if shortlist:
        direct = sum(1 for j in shortlist if not is_consultancy(j["company"]))
        contact = sum(1 for j in shortlist if j["contact"] != "-")
        stated = sum(1 for j in shortlist if j["salary_basis"] == "Stated")
        ws.append(["  ...from direct employers", direct])
        ws.append(["  ...with a CTC actually published", stated])
        ws.append(["  ...with a direct email/phone in the JD", contact])
    for c in ws["A"]:
        c.font = Font(bold=True)
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 46


def write_excel(shortlist, near_misses, stats, path):
    wb = Workbook()
    write_sheet(wb.active, shortlist, "Shortlist")
    write_sheet(wb.create_sheet(), near_misses, "Near Misses")
    write_summary(wb.create_sheet(), stats, shortlist)
    wb.save(path)


# ------------------------- main -------------------------

def collect(page):
    naukri_queries = list(PRIMARY_QUERIES)
    if INCLUDE_HIDDEN_SEARCHES:
        naukri_queries += HIDDEN_QUERIES

    raw, naukri_headers = [], None
    if "naukri" in SOURCES:
        try:
            print("[Naukri]   warming up session (1 navigation)...")
            naukri_headers = warm_naukri(page, naukri_queries[0], LOCATIONS[0])
            if naukri_headers:
                print("    got API token - using the fast path.")
                raw += fetch_naukri(page, naukri_queries, naukri_headers)
            else:
                raw += fetch_naukri_dom(page, naukri_queries)
        except Exception as e:
            print(f"  ! Naukri search failed: {e}")
    if "linkedin" in SOURCES:
        try:
            page.goto("https://www.linkedin.com/jobs",
                      wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(800)
            raw += fetch_linkedin(page, LINKEDIN_QUERIES)
        except Exception as e:
            print(f"  ! LinkedIn search failed: {e}")
    if "workday" in SOURCES:
        # Workday needs no browser at all - it is a plain JSON API per
        # employer - so it runs off its own thread pool and cannot be
        # slowed down or blocked by whatever the two job boards are doing.
        try:
            import workday_source
            raw += workday_source.fetch_workday(max_days_old=MAX_DAYS_OLD)
        except Exception as e:
            print(f"  ! Workday search failed: {e}")
    return raw, naukri_headers


def main():
    t_start = time.time()
    print("JOB BOT - India finance roles")
    print(f"  cities : {', '.join(LOCATIONS)}")
    print(f"  window : last {MAX_DAYS_OLD} days")
    print(f"  salary : {SALARY_MIN_LPA:g}-{SALARY_MAX_LPA:g} LPA "
          + ("(hard gate)" if SALARY_IS_A_GATE else "(scoring only, nothing dropped)"))
    print(f"  excel  : split at odds >= {INTERVIEW_MIN_SCORE}; "
          f"the website publishes everything\n")

    n_queries = len(PRIMARY_QUERIES) + (
        len(HIDDEN_QUERIES) if INCLUDE_HIDDEN_SEARCHES else 0)
    print(f"  search : {n_queries} keyword stems x {len(LOCATIONS)} cities "
          f"(no resume is read - matching happens in the visitor's browser)\n")

    from playwright.sync_api import sync_playwright
    wanted = wanted_location_tokens()
    raw, stage, naukri_headers = [], [], None

    with sync_playwright() as p:
        page, closer = open_browser(p, headless=not SHOW_BROWSER)
        try:
            raw, naukri_headers = collect(page)
            print(f"\nCollected {len(raw)} raw rows "
                  f"in {time.time() - t_start:.0f}s.")

            # ---- cheap gates first, so enrichment only touches survivors ----
            seen_ids, seen_keys = set(), set()
            stage = []
            d_dupe = d_old = d_loc = d_ar = d_named = 0
            for job in raw:
                if job["job_id"] in seen_ids:
                    d_dupe += 1
                    continue
                seen_ids.add(job["job_id"])
                key = dedupe_key(job)
                if key in seen_keys:
                    d_dupe += 1
                    continue
                seen_keys.add(key)

                if job["days_old"] is not None and job["days_old"] > MAX_DAYS_OLD:
                    d_old += 1
                    continue
                if not passes_location(job, wanted):
                    d_loc += 1
                    continue
                if REQUIRE_NAMED_COMPANY and is_confidential_company(job["company"]):
                    d_named += 1
                    continue
                # Pre-screen. Only the cheap, title-based half runs here:
                # every source now gets its full JD fetched, so anything
                # that depends on description text waits for the second
                # gate rather than being judged on a Naukri stub.
                keep, family, strength, why = finance_gate(job)
                job["_family"] = family
                job["_family_strength"] = strength
                # A hard-blocked title ("Software Engineer") is wrong no
                # matter what the JD later says, so that one cut applies
                # to every source. Everything else gets to wait for text.
                if not keep and ("different job family" in why
                                 or "ERP implementation" in why
                                 or "adjacent department" in why):
                    d_ar += 1
                    continue
                stage.append(job)

            print(f"After dedupe + city/date/title pre-screen: {len(stage)}")

            # ---- enrich the survivors (this is what makes rows decidable) ---
            if ENRICH_SHORTLIST and stage:
                # Enrich the freshest first - that's where the odds live.
                stage.sort(key=lambda j: (j["days_old"] is None,
                                          j["days_old"] or 99))
                try:
                    enrich_linkedin(page, stage)
                except Exception as e:
                    print(f"  (LinkedIn enrichment skipped: {e})")
                try:
                    enrich_naukri(page, stage, naukri_headers)
                except Exception as e:
                    print(f"  (Naukri enrichment skipped: {e})")
                try:
                    import workday_source
                    workday_source.enrich_workday(stage)
                except Exception as e:
                    print(f"  (Workday enrichment skipped: {e})")
        finally:
            try:
                closer.close()
            except Exception:
                pass

    # ---- mass-repost detection needs the full JD, so it runs post-enrich ----
    sig_companies = {}
    for job in stage:
        sig = jd_signature(job)
        job["_jd_sig"] = sig
        if sig:
            sig_companies.setdefault(sig, set()).add(job["company"].lower())
    for job in stage:
        sig = job.get("_jd_sig")
        job["_dup_company_count"] = len(sig_companies[sig]) if sig else 1

    # ---- gates that need the enriched JD -------------------------------
    scored = []
    d_salary = d_ar2 = d_thin = 0
    for job in stage:
        keep, band, basis, penalty = salary_verdict(job)
        if not keep:
            d_salary += 1
            continue
        # Re-classify now that the full description is in hand. A row whose
        # title said nothing ("Process Associate") usually lands in a real
        # family here, which is exactly the kind of job the old AR-only
        # build threw away by the thousand.
        keep_fam, family, strength, _why = finance_gate(job)
        if not keep_fam:
            d_ar2 += 1
            continue
        job["_family"] = family
        job["_family_strength"] = strength
        if (job.get("_full_jd") and
                len(job["description"]) < MIN_DESCRIPTION_CHARS):
            d_thin += 1
            continue
        job["salary_band"] = band
        job["salary_basis"] = basis
        job.update(interview_score(job, penalty))
        scored.append(job)

    scored.sort(key=lambda j: (j["score"], j["direct"], j["fresh"], j["comp"]),
                reverse=True)
    shortlist = [j for j in scored if j["score"] >= INTERVIEW_MIN_SCORE]
    near = [j for j in scored if j["score"] < INTERVIEW_MIN_SCORE]

    by_family = {}
    for j in scored:
        fam = j.get("family") or "Finance"
        by_family[fam] = by_family.get(fam, 0) + 1

    stats = [
        ("Raw rows pulled", len(raw)),
        ("Dropped - duplicate req", d_dupe),
        (f"Dropped - older than {MAX_DAYS_OLD}d", d_old),
        ("Dropped - wrong city", d_loc),
        ("Dropped - unnamed/confidential employer", d_named),
        ("Dropped - not a finance role (title)", d_ar),
        ("Dropped - not a finance role (full JD)", d_ar2),
        ("Dropped - no salary evidence at all", d_salary),
        ("Dropped - JD too thin to judge", d_thin),
        ("Published to the site", len(scored)),
    ]
    stats += [(f"   {fam}", n)
              for fam, n in sorted(by_family.items(), key=lambda kv: -kv[1])]

    print("\nFilter summary")
    for label, value in stats:
        print(f"  {label:<48}: {value}")
    print(f"  {'SHORTLISTED':<48}: {len(shortlist)}")

    # The spreadsheet stays the OWNER's view: their band, their cities,
    # split at their bar, and capped so it opens quickly. The website gets
    # everything - see the export call below.
    out = resolve(OUTPUT_FILE)
    try:
        write_excel(shortlist, near[:150], stats, out)
        saved = out
    except PermissionError:
        stamp = datetime.now().strftime("%H%M%S")
        saved = resolve(OUTPUT_FILE.replace(".xlsx", "") + "_" + stamp + ".xlsx")
        print(f"  ! '{OUTPUT_FILE}' is open in Excel - saving as "
              f"'{os.path.basename(saved)}' instead.")
        write_excel(shortlist, near[:150], stats, saved)

    # The website payload. Unlike the Excel file this never fails on a
    # locked handle, so it is written unconditionally.
    try:
        import export_site_data
        export_site_data.export_site_json(
            shortlist, near, stats,
            {
                "salary_min": SALARY_MIN_LPA,
                "salary_max": SALARY_MAX_LPA,
                "cities": LOCATIONS,
                "families": FAMILY_NAMES,
                "max_days_old": MAX_DAYS_OLD,
                "min_score": INTERVIEW_MIN_SCORE,
                "sources": SOURCES,
            },
            resolve("jobs.json"))
    except Exception as e:
        print(f"  ! Site export failed: {e}")

    print(f"\nDone in {time.time() - t_start:.0f}s.")
    print(f"  {len(scored)} jobs published  "
          f"({len(shortlist)} above your own bar of {INTERVIEW_MIN_SCORE})")
    print(f"  -> {saved}")
    if not scored:
        print("\n  Nothing collected this run. Try, in this order:")
        print("    1) MAX_DAYS_OLD = 14")
        print("    3) SHOW_BROWSER = True (a portal may want a CAPTCHA cleared)")


if __name__ == "__main__":
    main()
