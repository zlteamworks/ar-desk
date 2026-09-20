import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import finance_catalog as catalog
import naukri_job_bot as bot
import export_site_data as exporter
from build_site import build


class FinanceCoverageTests(unittest.TestCase):
    def test_linkedin_covers_families_before_deeper_pages(self):
        queries = ["accounts receivable", "actuarial", "investment banking"]
        urls = bot.linkedin_search_urls(queries)
        self.assertEqual(urls[:3], [bot.linkedin_search_url(q, bot.LINKEDIN_CITIES[0], 0) for q in queries])

    def test_countrywide_linkedin_and_workday_coverage_config(self):
        import workday_source
        self.assertEqual(bot.LINKEDIN_CITIES, ["India"])
        self.assertIn(("Mastercard", "mastercard", "wd1", "CorporateCareers"), workday_source.TENANTS)
        self.assertIn(("Visa", "visa", "wd5", "Visa"), workday_source.TENANTS)
        self.assertEqual(len(workday_source.TENANTS), len(set(workday_source.TENANTS)))
        self.assertGreaterEqual(workday_source.DETAIL_LIMIT, 300)

    def test_specialist_classification_and_collection_gates(self):
        cases = {
            "Finance Intern": "Accounting",
            "Chief Financial Officer": "Finance Leadership",
            "Credit Risk Analyst": "Risk Management",
            "Credit Analyst - Banking": "Banking & Lending",
            "AML KYC Analyst": "Financial Crime & KYC",
            "Equity Research Associate": "Investment Research",
            "SAP FICO Functional Consultant": "Finance Systems & Transformation",
            "Insurance Sales Manager": "Insurance",
            "Actuarial Analyst": "Actuarial",
            "Fund Accountant": "Fund Accounting",
            "Trade Finance Analyst": "Trade Finance",
            "Portfolio Manager": "Asset & Wealth Management",
            "Financial Controller": "Finance Leadership",
            "Cost Accountant": "Cost & Management Accounting",
            "Accounts Payable Executive": "Accounts Payable",
            "Accounts Receivable Analyst": "Accounts Receivable",
            "Quantitative Analyst": "Quantitative Finance",
        }
        for title, family in cases.items():
            with self.subTest(title=title):
                keep, actual, _, _ = bot.finance_gate({"title": title})
                self.assertTrue(keep)
                self.assertEqual(actual, family)
        for title in ["Software Engineer", "Recruiter - Banking", "Logistics Manager", "Taxidermist"]:
            self.assertFalse(bot.finance_gate({"title": title})[0], title)

    def test_internal_does_not_mean_internship(self):
        self.assertNotEqual(bot.detect_employment_type("Internal Audit Manager"), "Internship")
        self.assertEqual(bot.detect_employment_type("Finance Intern"), "Internship")

    def test_priority_does_not_depend_on_candidate_or_salary(self):
        job = {"days_old": 1, "posted_via": "direct", "applicants": 12}
        expected = catalog.listing_priority(job)
        for years, salary in [(0, 3), (5, 12), (25, 90)]:
            self.assertEqual(catalog.listing_priority(dict(job, exp_lo=years, sal_lo=salary)), expected)

    def test_requirement_export_respects_link_only_and_redaction(self):
        job = {"source": "LinkedIn", "description": "CFA required. Email person@example.com"}
        row = exporter._row(job, "shortlist", 0)
        self.assertEqual(row["requirement_text"], "")
        self.assertEqual(row["jd"], "")
        job["source"] = "Naukri"
        job["description"] = "Role summary. " * 180 + "CFA required. Send certificate to person@example.com."
        row = exporter._row(job, "shortlist", 0)
        self.assertIn("CFA required", row["requirement_text"])
        self.assertNotIn("person@example.com", row["requirement_text"])
        self.assertTrue(row["jd_truncated"])

    def test_public_contact_is_explicit_and_sanitized(self):
        base = {"source": "Naukri", "description": "Send CV to hiring@example.com. Call.9940085723"}
        row = exporter._row(dict(base, contact="hiring@example.com"), "shortlist", 0)
        self.assertEqual(row["contact"], "hiring@example.com")
        self.assertNotIn("hiring@example.com", row["jd"])
        self.assertNotIn("9940085723", row["jd"])
        row = exporter._row(dict(base, contact="-"), "shortlist", 0)
        self.assertEqual(row["contact"], "Not Available")

    def test_build_reclassifies_without_mutating_input(self):
        payload = {"jobs": [{"title": "Fund Accountant", "family": "Accounting", "score": 9,
                              "salary_basis": "Est. (from experience)", "salary_band": "10-15", "sal_lo": 10, "sal_hi": 15}]}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "index.html"
            build(payload, out_path=str(out), contacts_path=None)
            html = out.read_text(encoding="utf-8")
        data = json.loads(html.split('<script id="jobs-data" type="application/json">')[1].split('</script>')[0])
        self.assertEqual(data["jobs"][0]["family"], "Fund Accounting")
        self.assertIsNone(data["jobs"][0]["sal_lo"])
        self.assertEqual(payload["jobs"][0]["score"], 9)
        self.assertNotIn("/*__FINANCE_MATCH__*/", html)
        self.assertEqual(len(data["finance_catalog"]["families"]), 26)

    def test_javascript_screening(self):
        import shutil
        node = shutil.which("node")
        if not node:
            try:
                import playwright
                node = str(Path(playwright.__file__).parent / "driver" / "node.exe")
            except ImportError:
                self.skipTest("Node or Playwright's bundled Node is needed")
        result = subprocess.run([node, "tests/test_finance_match.js"],
                                input=json.dumps(catalog.public_catalog()), text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
