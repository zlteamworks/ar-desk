# TalentTap — finance job board

A self-updating job board for Indian accounts-receivable and finance roles.
A bot collects postings from Naukri, LinkedIn and Workday twice a day,
scores each one, and bakes the results into a single web page.

**Live site:** https://zlteamworks.github.io/ar-desk/

## What the page does

The published page is one self-contained HTML file — no backend, no API
calls at view time. Everything below runs in your own browser:

- **Search** titles, companies, locations and skills as you type. Combine
  keywords with the filters below, or clear the search to browse again.
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

## Current run

3,488 scored postings — 2,237 shortlist, 1,251 near-miss — of which 3,318
carry enough job text to match a resume against.

Collection targets roles at ₹10–15 LPA, posted within 7 days, across
Bengaluru, Chennai, Hyderabad, Mumbai, Pune, Delhi, Gurgaon, Noida,
Kolkata and Coimbatore.

## What is deliberately not published

- **Recruiter contact details.** Job posters often leave a personal email
  or mobile number in the description. `redact_contacts()` in
  [export_site_data.py](export_site_data.py) strips those from every
  published field before the page is built. They are not in this repo and
  not on the site.
- **LinkedIn job text.** LinkedIn rows carry title, company, location and
  a link out to the original posting, nothing more. `strip_for_publication()`
  enforces this.
- **Login sessions, resumes and the run spreadsheet.** All excluded by
  [.gitignore](.gitignore) and kept on the machine that runs the bot.

## Analytics and the daily report

Both are **off until configured**, and the page loads no analytics script
at all while they are.

There is no sign-in on TalentTap, so there are no user accounts to count.
What is measurable is how many people opened the link, how many were
distinct, where they came from, and how many did something once they
arrived — filtered, sorted, scored a resume, opened a job, or clicked
through to apply. That last group is the "actively used it" number.

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
