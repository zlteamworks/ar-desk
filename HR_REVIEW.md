# TalentTap: recruiter review and improvement roadmap

Reviewed 20 September 2026 against the live site and local collection,
publication and resume-matching code. Audience: finance careers in India,
from entry level to leadership, including banking, investment and insurance.

## Main finding

The strongest feature is seeing job requirements beside your own resume
without sending the resume to a server. The main weaknesses were uneven
career coverage and wording that made keyword overlap sound like a hiring
decision. A recruiter's decision can also depend on experience, credentials,
availability and application answers that this site cannot verify.

Employer-configured application answers can trigger automatic rejection;
there is no universal resume percentage that predicts this. See
[Greenhouse's auto-reject documentation](https://support.greenhouse.io/hc/en-us/articles/360000653472-Auto-reject)
and [Ashby's application rules](https://www.ashbyhq.com/product-updates/global-application-questions-and-auto-reject-rules).

## Changes implemented locally

- Expanded to 26 career families and added collection queries across Naukri,
  LinkedIn and Workday. The existing ten accounting families are joined by
  finance leadership, finance systems, financial crime/KYC, actuarial,
  insurance, quantitative finance, risk, investment banking/deals, investment
  research, asset/wealth management, fund accounting, investment operations,
  trade finance, banking/lending, cost accounting and sustainability finance.
- Added a career guide with typical skills and actual listing counts,
  including an honest empty state for categories without collected jobs.
- Added employment-type filtering and corrected the bug that read
  "internal audit" as an internship.
- Added a screening panel with source excerpts, explicit versus preferred
  requirements, ambiguous alternatives and manual checks for experience,
  qualifications, shifts, joining dates and work authorization.
- Added equivalent-name matching, basic negation handling and separate
  treatment of partially completed credentials. These remain text heuristics.
- Renamed the public ranking to listing priority; removed the owner's
  experience and salary preferences from that ranking. Unpublished pay
  remains unknown rather than being guessed from experience.
- Added mobile viewport metadata and revised overstated ATS/skill claims.

The categories are informed by the breadth of career areas in
[ACCA's Career Navigator](https://careernavigator.accaglobal.com/gb/en.html)
and [CFA Institute's career paths](https://www.cfainstitute.org/programs/cfa-program/careers).
The specific grouping and implementation are this project's design choices.

## What a candidate should see

| Posting evidence | Useful guidance | Avoid claiming |
| --- | --- | --- |
| "SAP experience mandatory"; no SAP evidence in resume | Add a truthful task/result example if you have it; otherwise identify a learning gap | "HR will reject you" |
| "Power BI preferred" | A useful differentiator, not automatically a blocker | Treating preferred skills as mandatory |
| "CA required"; resume says CA Inter | Confirm the required qualification and your completion status | Counting CA Inter as qualified CA |
| "SAP or Oracle required" | Review the alternative and relevant experience | Saying both tools are mandatory |
| Relevant experience, shifts or joining-date conditions | Verify against the full posting and your circumstances | Guessing these facts from a CV |
| No description or truncated description | Open the original employer posting | Reporting complete eligibility |

Candidates should demonstrate domain tasks and results, not add keyword
lists they cannot defend. Suggested evidence examples: an AR reconciliation
or dispute outcome; an FP&A forecast and variance explanation; a valuation
model with assumptions; an AML case review; an actuarial modelling project.
Projects are useful early-career evidence but do not replace experience or
credentials explicitly required by an employer.

## Recommended next work, in order

1. **Improve source coverage and freshness.** Measure roles returned per
   family and source, fetch success, duplicates and expired links. Add
   verified employer feeds for banks, insurers and investment firms. A list
   of categories alone does not guarantee a supply of vacancies.
2. **Add a private application tracker.** Save jobs, record application date,
   status and follow-up notes, and export them. Start with browser storage
   and an explicit explanation of persistence.
3. **Make work conditions easier to compare.** Add stated remote/hybrid/office
   mode, employment terms, relevant experience and pay ranges, always keeping
   missing information separate from an inferred value.
4. **Offer a targeted evidence checklist.** Let candidates choose a role,
   then record examples of the required work. Keep learning resources and
   portfolio exercises separate from the employer's actual requirements.
5. **Improve resume privacy controls.** Offer an explicit "remember on this
   device" choice. The existing browser storage persists until removed;
   shared-computer users should understand that.
6. **Add opt-in alerts after coverage is reliable.** Saved searches and email
   digests need consent, unsubscribe handling and a backend; do not promise
   them through the current static page alone.

## Limits and rollout

The expanded collection will increase source requests and may take longer.
LinkedIn's existing time limit and Workday's detail cap still constrain
coverage. Rebuilding uses the current dataset; new career queries take effect
on the next collector run. Legacy rows have only the existing description
excerpt; the next export also retains redacted requirement excerpts where
publication is permitted.

The matching vocabulary and sentence rules are deliberately conservative,
but cannot fully interpret a job description. They cannot authenticate a
credential, assess proficiency, or reproduce an employer's ATS configuration.
No universal rejection score or guarantee is offered.

Validation covers specialist classification, non-finance exclusions, aliases,
partial credentials, negation, required/preferred clauses, alternatives,
link-only publication, contact redaction and candidate-independent priority.
Browser checks cover search, filters, resume insertion/removal, the career
guide, screening panels and narrow layouts.

Deployment must include the new source modules and template alongside the
generated page. The existing daily refresh script stages only data and
generated HTML; expanded searches take effect on its next collector run.
