import sqlite3
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "cloudflare" / "src" / "worker.js"
SCHEMA = ROOT / "cloudflare" / "schema.sql"
WRANGLER = ROOT / "cloudflare" / "wrangler.toml"
NODE = Path.home() / "AppData/Roaming/Python/Python313/site-packages/playwright/driver/node.exe"


class CloudflareDeploymentTests(unittest.TestCase):
    def test_d1_schema_initializes_and_enforces_roles(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertTrue({"users", "candidate_profiles", "sessions", "audit_log", "auth_attempts"} <= tables)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO users(user_id,email,password_salt,password_hash,password_iterations,role,status,full_name,created_at) "
                "VALUES('1','a@example.com','salt','hash',100000,'admin','active','A','2026-01-01')"
            )

    def test_worker_contains_required_access_controls(self):
        source = WORKER.read_text(encoding="utf-8")
        self.assertIn('name: "PBKDF2"', source)
        self.assertIn("recordLoginFailure", source)
        self.assertNotIn("firebase", source.lower())
        self.assertIn("user.role !== \"job_seeker\"", source)
        self.assertIn("user.role !== \"hr\"", source)
        self.assertIn("u.status='active'", source)
        self.assertIn("contact_consent", source)
        self.assertIn("path === \"/index.html\"", source)
        self.assertIn("HttpOnly; Secure; SameSite=Strict", source)

    def test_job_bar_is_inserted_at_real_page_header(self):
        source = WORKER.read_text(encoding="utf-8")
        self.assertNotIn('body.replace("<body>"', source)
        self.assertIn('const pageAnchor = `<header class="masthead">`', source)
        self.assertIn("body.replace(pageAnchor, bar + pageAnchor)", source)

    def test_worker_has_valid_javascript_syntax(self):
        if not NODE.exists():
            self.skipTest("Node is not available in this environment")
        result = subprocess.run(
            [str(NODE), "--check", str(WORKER)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrangler_binds_assets_and_database(self):
        config = WRANGLER.read_text(encoding="utf-8")
        self.assertIn('binding = "ASSETS"', config)
        self.assertIn('binding = "DB"', config)
        self.assertIn('run_worker_first = true', config)


if __name__ == "__main__":
    unittest.main()
