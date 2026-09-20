# TalentTap — finance job board

A self-updating job board for finance careers in India, from internships
to leadership, including accounting, banking, investment and insurance.
A bot collects postings from Naukri, LinkedIn and Workday twice a day,
scores each one, and bakes the results into a job page. A role-based website
backend protects that page and provides private candidate/HR workspaces.

**Live site:** https://talenttap.talenttap-finance-india.workers.dev/

## What the page does

The generated job page is one self-contained HTML file and performs matching
in the visitor's browser. For accounts and private profiles, deploy the
website backend in [`cloudflare/`](cloudflare/); GitHub Pages cannot enforce login.

- **Search** titles, companies, locations and skills as you type. Combine
  keywords with the filters below, or clear the search to browse again.
- **Explore 26 career areas** and their typical skills in the career guide.
  Collection queries and classification share [finance_catalog.py](finance_catalog.py).
- **Review screening evidence.** Explicit requirements, preferred skills,
  alternatives and conditions to verify are shown with posting excerpts.
  A missing term is not proof of a missing skill or a rejection prediction.
- **Filter** by job family (AR, Credit & Collections, AP, Payroll, Tax,
  Audit, Treasury, FP&A, R2R, Accounting), city, salary band, experience
  and how recently the job was posted.
- **Score your own resume** against any posting. Drop in a resume and the
  page matches it against the job text locally. Your resume is never
  uploaded or transmitted — there is nowhere for it to go.
- **See the skill gap on the card.** Once a resume is loaded, each row
  shows what the posting asks for that the resume never mentions — without
  opening anything. Deciding whether to apply shouldn't cost a click.
- **See who you'd actually reach.** Each row is labelled `direct`,
  `agency` or `unverified`. Only postings from an employer's own applicant
  tracking system, or a recognised staffing firm, get a definite label;
  the rest are marked unverified rather than guessed at.

## Keywords collect, resumes only match

The two halves are deliberately separate, and only one of them is personal.

**Collection and ranking are keyword-driven and identical for everyone.**
The portals are asked for the stems in `PRIMARY_QUERIES`, `HIDDEN_QUERIES`
and `LINKEDIN_QUERIES` in [naukri_job_bot.py](naukri_job_bot.py), across
`LOCATIONS`. The interview-odds number is built from directness,
freshness, competition, family fit and experience band — nothing else.
No resume is read anywhere in the bot. The public site's **listing priority**
is recalculated separately from employer verification, freshness and reported
applicant counts. It does not use the owner's salary or experience, and is
not an interview probability.

**Matching is per visitor and stays on their machine.** The resume upload
on the page parses and scores the CV in the browser, recomputes every
row's match, and derives the skill gap from the posting's own asks. It is
never uploaded, and it never touches the published numbers.

This used to be muddier: the score carried a 12% "does the owner's CV
cover this JD" term, and the skills column was a diff against that same
CV — a private number shipped to strangers who could not read it. Both
are gone. `Skills the Posting Asks For` now means exactly that.

## Current run

The website shows the current run's counts and collection timestamp.
Collection accepts all salary bands and experience levels, targets postings
within seven days and searches 15 Indian hiring hubs. Broader career queries
take effect on the next collection; rebuilding reclassifies existing public
rows but does not fetch new jobs. Coverage depends on source availability,
search budgets and employer feeds; it is not an exhaustive census of jobs.

The career guide's examples are not eligibility rules. Screening uses only
available posting text, treats equivalent skill names together and keeps
partial qualifications such as CA Inter distinct from qualified CA. Link-only
sources retain no job description or derived requirement text. Read the full
posting before applying; the tool cannot verify competence or credentials.

See [the recruiter review and roadmap](HR_REVIEW.md) for the findings and
recommended next improvements.

Validation: `python -m unittest discover -s tests -v`. The JavaScript checks
require Node.js or the Node runtime bundled with Playwright.

## Publication and privacy boundaries

- **Recruiter contact details are explicit and source-bound.** When a job
  poster includes an email address or Indian mobile number in the source
  posting, the exporter validates it and publishes it only in the dedicated
  recruiter-contact field. Titles, company names and description text remain
  contact-redacted, and the interface shows `Not Available` rather than
  guessing when no direct detail was supplied.
- **LinkedIn job text.** LinkedIn rows carry title, company, location and
  a link out to the original posting, nothing more. `strip_for_publication()`
  enforces this.
- **Login sessions, account data, resumes and the run spreadsheet.** All
  databases and backups are excluded by [.gitignore](.gitignore). Candidate
  contacts are visible only to approved HR accounts and only after the
  candidate separately enables discovery and contact sharing. The
  `*.docx` / `*.pdf` rule stays even though the bot no longer reads a
  resume — a CV left in this folder should still never be committable.

## Analytics and the daily report

Both are **off until configured**, and the page loads no analytics script
at all while they are.

The optional account server keeps its own audit events for registrations,
logins, HR approval and profile changes. Public-page product analytics remain
separate and record only aggregate use such as filtering, resume matching,
opening a job or clicking through to apply.

Counting runs through [GoatCounter](https://www.goatcounter.com): no
cookies, no cross-site identifiers, nothing that identifies a person,
which is why the page carries no consent banner. To switch it on, set
`ANALYTICS_CODE` near the top of the script in
[site_template.html](site_template.html) to your GoatCounter site code and
rebuild.

[daily_report.py](daily_report.py) mails the day's numbers each evening,
driven by [.github/workflows/daily-report.yml](.github/workflows/daily-report.yml)
at 21:00 IST. It reads its configuration from repository secrets —
`GOATCOUNTER_CODE`, `GOATCOUNTER_TOKEN`, `SMTP_USER`, `SMTP_PASS` and
`REPORT_TO` — so no credential is ever committed. Standard library only;
nothing to install.

## Running it yourself

```
python naukri_job_bot.py    # collect and score -> jobs.xlsx + jobs.json
python build_site.py        # bake jobs.json into site/index.html
```

Pushing to `main` deploys the page — see
[.github/workflows/pages.yml](.github/workflows/pages.yml).

`refresh_site.ps1` chains all three and is registered with Windows Task
Scheduler as "AR Desk refresh" (7am and 7pm daily), so the live link keeps
showing the latest run without anyone touching it.

Requires Python 3.12+ and a logged-in browser profile for the portals that
need one.

## Protected account platform

The recommended free deployment is the all-in-one TalentTap website in
[`cloudflare/README.md`](cloudflare/README.md). Registration, login, role
selection, profiles and candidate discovery are pages within TalentTap; users
do not install or sign into a separate app. Cloudflare supplies only the
server-side hosting and private D1 database.

GitHub Pages serves static files and therefore cannot securely implement the
requested account restriction. Run the application server on an HTTPS host
with a persistent encrypted disk:

```powershell
python talenttap_server.py --init-db
$env:TALENTTAP_SECURE_COOKIE = "0"   # local HTTP only; never production
python talenttap_server.py --serve
```

Job-seeker accounts activate immediately. HR accounts remain pending so a
visitor cannot self-identify as a recruiter and harvest candidate contacts.
After checking the person's company and hiring role:

```powershell
python talenttap_server.py --approve-hr recruiter@company.com
```

Candidate profiles appear in the HR directory only when `actively looking`
is enabled. Email and phone require a second, explicit contact-sharing
consent. Passwords use salted PBKDF2-SHA256; session tokens are stored only as
hashes; authenticated writes use CSRF tokens; login/registration are rate
limited. The service creates one consistent SQLite backup per day and retains
30 generations. An on-demand backup is `python talenttap_server.py --backup`.
Copy the `backups/` directory to encrypted off-site storage according to your
retention and deletion policy; repository copies are intentionally blocked.

For deployment requirements and the GitHub Pages migration, see
[DEPLOYMENT.md](DEPLOYMENT.md).
