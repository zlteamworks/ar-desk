import sqlite3
from pathlib import Path
import unittest

import talenttap_server as server


class TalentTapServerTests(unittest.TestCase):
    def setUp(self):
        self.db_path = Path.cwd() / ".talenttap-test.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.db_path) + suffix).unlink(missing_ok=True)
        server.init_db(self.db_path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.db_path) + suffix).unlink(missing_ok=True)

    def test_password_hash_is_salted_and_verifiable(self):
        first = server.password_hash("Finance1234")
        second = server.password_hash("Finance1234")
        self.assertNotEqual(first, second)
        self.assertTrue(server.verify_password("Finance1234", first))
        self.assertFalse(server.verify_password("wrong", first))

    def test_roles_profiles_and_hr_approval(self):
        with server.connect(self.db_path) as db:
            seeker = db.execute("""INSERT INTO users
                (email,password_hash,role,status,full_name,created_at)
                VALUES(?,?,?,?,?,?)""", ("candidate@example.com", "hash", "job_seeker", "active", "Candidate", server.now_iso())).lastrowid
            db.execute("INSERT INTO seeker_profiles(user_id,actively_looking,contact_consent,updated_at) VALUES(?,?,?,?)",
                       (seeker, 1, 1, server.now_iso()))
            db.execute("""INSERT INTO users
                (email,password_hash,role,status,full_name,company,designation,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", ("hr@example.com", "hash", "hr", "pending", "Recruiter", "Example", "Manager", server.now_iso()))
        self.assertTrue(server.approve_hr("hr@example.com", self.db_path))
        with server.connect(self.db_path) as db:
            self.assertEqual(db.execute("SELECT status FROM users WHERE email='hr@example.com'").fetchone()[0], "active")
            visible = db.execute("""SELECT COUNT(*) FROM seeker_profiles p JOIN users u ON u.id=p.user_id
                WHERE p.actively_looking=1 AND u.status='active'""").fetchone()[0]
            self.assertEqual(visible, 1)

    def test_session_tokens_are_stored_as_hashes(self):
        with server.connect(self.db_path) as db:
            user_id = db.execute("""INSERT INTO users
                (email,password_hash,role,status,full_name,created_at)
                VALUES(?,?,?,?,?,?)""", ("candidate@example.com", "hash", "job_seeker", "active", "Candidate", server.now_iso())).lastrowid
            token, csrf = server.create_session(db, user_id)
            stored = db.execute("SELECT token_hash,csrf_token FROM sessions").fetchone()
        self.assertNotEqual(stored["token_hash"], token)
        self.assertEqual(stored["token_hash"], server.token_digest(token))
        self.assertEqual(stored["csrf_token"], csrf)


if __name__ == "__main__":
    unittest.main()
