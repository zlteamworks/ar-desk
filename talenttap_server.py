"""TalentTap account server.

Turns the generated job board into a role-gated, two-sided application using
only Python's standard library. Run behind an HTTPS reverse proxy in
production; GitHub Pages cannot host this server or protect private data.

    python talenttap_server.py --init-db
    python talenttap_server.py --approve-hr recruiter@company.com
    python talenttap_server.py --backup
    python talenttap_server.py --serve
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("TALENTTAP_DB", ROOT / "data" / "talenttap.sqlite3"))
BACKUP_DIR = Path(os.environ.get("TALENTTAP_BACKUP_DIR", ROOT / "backups"))
SITE_PATH = ROOT / "site" / "index.html"
SESSION_DAYS = 14
PBKDF2_ROUNDS = 310_000
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^(?:\+?91[-\s]?)?[6-9]\d{9}$")
LINKEDIN_RE = re.compile(r"^https://(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9._%-]+/?$", re.I)
RATE_LOCK = threading.Lock()
RATE_BUCKETS: dict[tuple[str, str], list[float]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback and close when used as a context manager."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def init_db(path: Path = DB_PATH) -> None:
    with connect(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('job_seeker','hr')),
            status TEXT NOT NULL CHECK(status IN ('active','pending','suspended')),
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            designation TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            last_login_at TEXT
        );
        CREATE TABLE IF NOT EXISTS seeker_profiles (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            headline TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            experience_years REAL,
            skills TEXT NOT NULL DEFAULT '',
            desired_roles TEXT NOT NULL DEFAULT '',
            notice_period TEXT NOT NULL DEFAULT '',
            work_mode TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            linkedin_url TEXT NOT NULL DEFAULT '',
            actively_looking INTEGER NOT NULL DEFAULT 0 CHECK(actively_looking IN (0,1)),
            contact_consent INTEGER NOT NULL DEFAULT 0 CHECK(contact_consent IN (0,1)),
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf_token TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            event TEXT NOT NULL,
            ip_hash TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_users_role_status ON users(role, status);
        CREATE INDEX IF NOT EXISTS idx_profiles_active ON seeker_profiles(actively_looking, contact_consent);
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
        """)


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ROUNDS,
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text + "==")
        expected = base64.urlsafe_b64decode(digest_text + "==")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
    db.execute(
        "INSERT INTO sessions(token_hash,user_id,csrf_token,created_at,expires_at) VALUES(?,?,?,?,?)",
        (token_digest(token), user_id, csrf, now_iso(), expires.isoformat(timespec="seconds")),
    )
    return token, csrf


def backup_database(source: Path = DB_PATH, destination: Path = BACKUP_DIR) -> Path:
    init_db(source)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = destination / f"talenttap-{stamp}.sqlite3"
    src = connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    backups = sorted(destination.glob("talenttap-*.sqlite3"), reverse=True)
    for old in backups[30:]:
        old.unlink(missing_ok=True)
    return target


def approve_hr(email: str, path: Path = DB_PATH) -> bool:
    with connect(path) as db:
        result = db.execute(
            "UPDATE users SET status='active' WHERE email=? AND role='hr'", (email.strip().lower(),)
        )
        if result.rowcount:
            user_id = db.execute("SELECT id FROM users WHERE email=?", (email.strip().lower(),)).fetchone()[0]
            db.execute("INSERT INTO audit_log(user_id,event,created_at) VALUES(?,?,?)",
                       (user_id, "hr_approved", now_iso()))
        return bool(result.rowcount)


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


BASE_CSS = """
:root{--navy:#101d36;--navy2:#263c6a;--teal:#0d8b7b;--mint:#8fe8d9;--purple:#6557d9;--paper:#f4f7fb;--ink:#132238;--muted:#687b91;--line:#dce4ed;--white:#fff;--danger:#b42336}*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#eef4fa,#f7f9fc);color:var(--ink);font-family:Arial,"Helvetica Neue",sans-serif;line-height:1.5}a{color:var(--teal)}.shell{min-height:100vh}.nav{height:74px;padding:0 max(24px,calc((100vw - 1180px)/2));display:flex;align-items:center;gap:18px;color:#fff;background:linear-gradient(135deg,var(--navy),var(--navy2))}.logo{display:flex;align-items:center;gap:10px;font-size:23px;font-weight:800;color:#fff;text-decoration:none}.logo-i{width:34px;height:34px;display:grid;place-items:center;border-radius:10px;background:#19b8a2}.navlinks{display:flex;gap:5px;margin-left:auto}.navlinks a,.logout{border:0;color:#dbe7f6;background:transparent;text-decoration:none;padding:9px 11px;border-radius:8px;font:600 13px Arial;cursor:pointer}.navlinks a:hover,.logout:hover{background:#ffffff14;color:#fff}.page{width:min(1180px,calc(100% - 32px));margin:34px auto 70px}.auth-page{display:grid;grid-template-columns:1fr 480px;gap:70px;align-items:center;min-height:calc(100vh - 145px)}.pitch h1{font-size:clamp(42px,6vw,72px);line-height:1.01;letter-spacing:-.055em;margin:0}.pitch h1 span{color:var(--teal)}.pitch p{max-width:610px;color:var(--muted);font-size:17px}.points{display:grid;gap:10px;margin-top:28px}.points span:before{content:'✓';color:var(--teal);font-weight:900;margin-right:9px}.card{background:#fff;border:1px solid var(--line);border-radius:18px;box-shadow:0 18px 50px #1c2d4a16}.auth-card{padding:30px}.auth-card h2{margin:0;font-size:27px}.sub{color:var(--muted);margin:5px 0 22px}.field{margin:14px 0}.field label{display:block;font-size:12px;font-weight:800;margin-bottom:5px}.field input,.field textarea,.field select,.search{width:100%;border:1px solid var(--line);border-radius:10px;padding:11px 12px;color:var(--ink);background:#fff;font:14px Arial}.field textarea{min-height:100px;resize:vertical}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.role-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.role{position:relative}.role input{position:absolute;opacity:0}.role label{display:block;border:1px solid var(--line);border-radius:12px;padding:14px;cursor:pointer}.role input:checked+label{border-color:var(--teal);box-shadow:0 0 0 3px #0d8b7b18;background:#effbf8}.role b,.role small{display:block}.role small{color:var(--muted);margin-top:3px}.btn{display:inline-flex;justify-content:center;align-items:center;min-height:43px;padding:0 17px;border:0;border-radius:10px;background:linear-gradient(135deg,var(--teal),#087367);color:#fff;font-weight:800;text-decoration:none;cursor:pointer}.btn.full{width:100%;margin-top:8px}.btn.alt{background:#eef3f8;color:var(--ink);border:1px solid var(--line)}.error,.notice{padding:10px 12px;border-radius:9px;font-size:13px;margin:12px 0}.error{background:#fce8eb;color:var(--danger)}.notice{background:#e4f5ef;color:#146b4c}.hero-card{padding:30px;background:linear-gradient(135deg,var(--navy),#3d358d);color:#fff}.hero-card h1{margin:0;font-size:34px}.hero-card p{color:#d7def0;max-width:720px}.stats{display:flex;gap:12px;flex-wrap:wrap;margin-top:20px}.stat{padding:12px 15px;border:1px solid #ffffff26;border-radius:11px;background:#ffffff0d}.stat b{display:block;font-size:21px}.section-head{display:flex;gap:20px;justify-content:space-between;align-items:end;margin:30px 0 14px}.section-head h2{margin:0}.filters{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:10px;padding:14px}.profiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.profile{padding:20px}.profile-top{display:flex;gap:12px;justify-content:space-between}.avatar{width:46px;height:46px;border-radius:13px;display:grid;place-items:center;background:#ddf5f0;color:var(--teal);font-weight:900}.identity{flex:1}.identity h3{margin:0}.identity p{margin:2px 0;color:var(--muted)}.badge{height:max-content;border-radius:999px;padding:4px 8px;background:#e0f4ea;color:#16845b;font-size:11px;font-weight:800}.chips{display:flex;gap:6px;flex-wrap:wrap;margin:15px 0}.chip{padding:4px 8px;border-radius:999px;background:#eff3f8;color:#41536b;font-size:12px}.summary{color:#52657c;font-size:13px}.contact{display:flex;gap:12px;flex-wrap:wrap;margin-top:14px;padding-top:13px;border-top:1px solid var(--line);font-size:13px}.empty{text-align:center;padding:55px 20px}.consent{padding:13px;border-radius:11px;background:#effbf8;border:1px solid #cce9e3}.pending{max-width:700px;margin:70px auto;padding:38px;text-align:center}.fine{font-size:12px;color:var(--muted)}@media(max-width:820px){.auth-page{grid-template-columns:1fr;gap:25px}.pitch{padding-top:20px}.auth-card{padding:22px}.profiles{grid-template-columns:1fr}.filters{grid-template-columns:1fr}.navlinks a{display:none}.grid2{grid-template-columns:1fr}.role-grid{grid-template-columns:1fr}}
"""


def layout(title: str, body: str, user: sqlite3.Row | None = None) -> str:
    if user:
        links = ('<a href="/jobs">Jobs</a><a href="/profile">My profile</a>'
                 if user["role"] == "job_seeker" else '<a href="/hr">Candidates</a>')
        nav = f"""<div class="nav"><a class="logo" href="/"><span class="logo-i">T</span>TalentTap</a>
        <div class="navlinks">{links}<form method="post" action="/logout"><input type="hidden" name="csrf" value="{esc(user['csrf_token'])}"><button class="logout">Sign out</button></form></div></div>"""
    else:
        nav = """<div class="nav"><a class="logo" href="/"><span class="logo-i">T</span>TalentTap</a><div class="navlinks"><a href="/login">Sign in</a><a href="/register">Create account</a></div></div>"""
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · TalentTap</title><style>{BASE_CSS}</style><body><div class="shell">{nav}{body}</div></body></html>"""


def auth_page(mode: str, error: str = "", values: dict | None = None) -> str:
    values = values or {}
    is_register = mode == "register"
    role = values.get("role", "job_seeker")
    extra = ""
    if is_register:
        extra = f"""
        <div class="field"><label>I am joining as</label><div class="role-grid">
          <div class="role"><input id="seek" type="radio" name="role" value="job_seeker" {'checked' if role == 'job_seeker' else ''}><label for="seek"><b>Job seeker</b><small>Find roles and become discoverable to approved HR teams.</small></label></div>
          <div class="role"><input id="hire" type="radio" name="role" value="hr" {'checked' if role == 'hr' else ''}><label for="hire"><b>HR / Recruiter</b><small>Search active, consented candidate profiles after verification.</small></label></div>
        </div></div>
        <div class="grid2"><div class="field"><label>Full name</label><input required maxlength="100" name="full_name" value="{esc(values.get('full_name'))}"></div><div class="field"><label>Phone</label><input maxlength="18" name="phone" value="{esc(values.get('phone'))}" placeholder="Optional for job seekers"></div></div>
        <div class="grid2"><div class="field"><label>Company (required for HR)</label><input maxlength="120" name="company" value="{esc(values.get('company'))}"></div><div class="field"><label>Designation</label><input maxlength="100" name="designation" value="{esc(values.get('designation'))}"></div></div>"""
    error_html = f'<div class="error">{esc(error)}</div>' if error else ""
    body = f"""<main class="page auth-page"><section class="pitch"><span class="badge">FINANCE TALENT, CONNECTED</span><h1>One platform.<br><span>Two clear paths.</span></h1><p>Relevant finance opportunities for candidates and a consent-first talent directory for verified hiring teams.</p><div class="points"><span>Role-based private experiences</span><span>Candidate-controlled contact visibility</span><span>Fresh jobs with source evidence</span></div></section><section class="card auth-card"><h2>{'Create your account' if is_register else 'Welcome back'}</h2><p class="sub">{'Tell us how you use TalentTap.' if is_register else 'Sign in to continue to your workspace.'}</p>{error_html}<form method="post" action="/{mode}">{extra}<div class="field"><label>Email</label><input required type="email" maxlength="254" autocomplete="email" name="email" value="{esc(values.get('email'))}"></div><div class="field"><label>Password</label><input required type="password" minlength="10" maxlength="128" autocomplete="{'new-password' if is_register else 'current-password'}" name="password"></div><button class="btn full">{'Create account' if is_register else 'Sign in'}</button></form><p class="fine">{'Already registered? <a href="/login">Sign in</a>' if is_register else 'New here? <a href="/register">Create an account</a>'}</p></section></main>"""
    return layout("Create account" if is_register else "Sign in", body)


def rate_allowed(ip: str, action: str, maximum: int = 10, window: int = 900) -> bool:
    current = time.time()
    key = (hashlib.sha256(ip.encode()).hexdigest()[:16], action)
    with RATE_LOCK:
        recent = [stamp for stamp in RATE_BUCKETS.get(key, []) if current - stamp < window]
        if len(recent) >= maximum:
            RATE_BUCKETS[key] = recent
            return False
        recent.append(current)
        RATE_BUCKETS[key] = recent
        return True


class TalentTapHandler(BaseHTTPRequestHandler):
    server_version = "TalentTap/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{now_iso()}] {self.client_address[0]} {fmt % args}")

    def send_html(self, content: str, status: int = 200, cookie: str | None = None) -> None:
        raw = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Security-Policy", "default-src 'self' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; worker-src 'self' blob: https://cdnjs.cloudflare.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def redirect(self, path: str, cookie: str | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def form(self) -> dict[str, str]:
        length = min(int(self.headers.get("Content-Length", "0")), 100_000)
        values = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
        return {key: value[-1].strip() for key, value in values.items()}

    def current_user(self) -> sqlite3.Row | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("tt_session")
        if not morsel:
            return None
        with connect() as db:
            return db.execute("""
                SELECT u.*, s.csrf_token FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>?""",
                (token_digest(morsel.value), now_iso()),
            ).fetchone()

    def require(self, role: str | None = None) -> sqlite3.Row | None:
        user = self.current_user()
        if not user:
            self.redirect("/login")
            return None
        if role and user["role"] != role:
            self.redirect("/jobs" if user["role"] == "job_seeker" else "/hr")
            return None
        return user

    def valid_csrf(self, user: sqlite3.Row, form: dict[str, str]) -> bool:
        return hmac.compare_digest(str(user["csrf_token"]), form.get("csrf", ""))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/health":
            self.send_html("ok")
            return
        if path in ("/login", "/register"):
            if self.current_user():
                self.redirect("/")
            else:
                self.send_html(auth_page(path[1:]))
            return
        if path == "/":
            user = self.current_user()
            if not user:
                self.redirect("/login")
            elif user["role"] == "hr":
                self.redirect("/hr")
            else:
                self.redirect("/jobs")
            return
        if path == "/jobs":
            user = self.require("job_seeker")
            if not user:
                return
            if not SITE_PATH.exists():
                self.send_html(layout("Jobs", '<main class="page"><div class="error">Run python build_site.py first.</div></main>', user), 503)
                return
            page = SITE_PATH.read_text(encoding="utf-8")
            toolbar = f"""<div style="position:sticky;top:0;z-index:100;background:#101d36;color:#fff;padding:9px 20px;display:flex;justify-content:space-between;align-items:center;font:13px Arial"><span>Signed in as <b>{esc(user['full_name'])}</b> · Job seeker</span><span><a href="/profile" style="color:#8fe8d9;margin-right:16px">My candidate profile</a><form style="display:inline" method="post" action="/logout"><input type="hidden" name="csrf" value="{esc(user['csrf_token'])}"><button style="color:#fff;background:transparent;border:1px solid #ffffff55;border-radius:6px;padding:5px 9px">Sign out</button></form></span></div>"""
            page = page.replace("<body>", "<body>" + toolbar, 1)
            self.send_html(page)
            return
        if path == "/profile":
            user = self.require("job_seeker")
            if user:
                self.profile_page(user)
            return
        if path == "/hr":
            user = self.require("hr")
            if user:
                self.hr_page(user, parse_qs(parsed.query))
            return
        self.send_html(layout("Not found", '<main class="page empty"><h1>Page not found</h1></main>'), 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        form = self.form()
        if path == "/register":
            self.register(form)
        elif path == "/login":
            self.login(form)
        elif path == "/logout":
            user = self.current_user()
            if user and self.valid_csrf(user, form):
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                if cookie.get("tt_session"):
                    with connect() as db:
                        db.execute("DELETE FROM sessions WHERE token_hash=?", (token_digest(cookie["tt_session"].value),))
                self.redirect("/login", "tt_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            else:
                self.send_html(layout("Forbidden", '<main class="page error">Invalid request.</main>'), 403)
        elif path == "/profile":
            user = self.require("job_seeker")
            if user and self.valid_csrf(user, form):
                self.save_profile(user, form)
            elif user:
                self.send_html(layout("Forbidden", '<main class="page error">Invalid request.</main>', user), 403)
        else:
            self.send_html(layout("Not found", '<main class="page empty"><h1>Page not found</h1></main>'), 404)

    def register(self, form: dict[str, str]) -> None:
        if not rate_allowed(self.client_address[0], "register", 6):
            self.send_html(auth_page("register", "Too many attempts. Please try again later.", form), 429)
            return
        email = form.get("email", "").lower()
        password, role = form.get("password", ""), form.get("role", "")
        name, phone = form.get("full_name", ""), form.get("phone", "")
        company, designation = form.get("company", ""), form.get("designation", "")
        error = ""
        if not EMAIL_RE.match(email): error = "Enter a valid email address."
        elif len(password) < 10 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password): error = "Use at least 10 characters with a letter and number."
        elif role not in ("job_seeker", "hr"): error = "Choose Job seeker or HR / Recruiter."
        elif not name or len(name) > 100: error = "Enter your full name."
        elif phone and not PHONE_RE.match(phone): error = "Enter a valid Indian mobile number or leave it blank."
        elif role == "hr" and (not company or not designation): error = "Company and designation are required for HR verification."
        if error:
            self.send_html(auth_page("register", error, form), 400)
            return
        status = "pending" if role == "hr" else "active"
        try:
            with connect() as db:
                cursor = db.execute("""INSERT INTO users(email,password_hash,role,status,full_name,phone,company,designation,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)""", (email, password_hash(password), role, status, name, phone, company, designation, now_iso()))
                user_id = cursor.lastrowid
                if role == "job_seeker":
                    db.execute("INSERT INTO seeker_profiles(user_id,updated_at) VALUES(?,?)", (user_id, now_iso()))
                db.execute("INSERT INTO audit_log(user_id,event,created_at) VALUES(?,?,?)", (user_id, "registered", now_iso()))
        except sqlite3.IntegrityError:
            self.send_html(auth_page("register", "An account with this email already exists.", form), 409)
            return
        self.redirect("/login")

    def login(self, form: dict[str, str]) -> None:
        if not rate_allowed(self.client_address[0], "login", 10):
            self.send_html(auth_page("login", "Too many attempts. Please try again later.", form), 429)
            return
        email, password = form.get("email", "").lower(), form.get("password", "")
        with connect() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if not user or not verify_password(password, user["password_hash"]) or user["status"] == "suspended":
                self.send_html(auth_page("login", "Email or password is incorrect.", {"email": email}), 401)
                return
            token, _ = create_session(db, user["id"])
            db.execute("UPDATE users SET last_login_at=? WHERE id=?", (now_iso(), user["id"]))
            db.execute("INSERT INTO audit_log(user_id,event,created_at) VALUES(?,?,?)", (user["id"], "login", now_iso()))
        secure = "; Secure" if os.environ.get("TALENTTAP_SECURE_COOKIE", "1") != "0" else ""
        cookie = f"tt_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_DAYS*86400}{secure}"
        self.redirect("/", cookie)

    def profile_page(self, user: sqlite3.Row, notice: str = "") -> None:
        with connect() as db:
            p = db.execute("SELECT * FROM seeker_profiles WHERE user_id=?", (user["id"],)).fetchone()
        notice_html = f'<div class="notice">{esc(notice)}</div>' if notice else ""
        checked = lambda key: "checked" if p and p[key] else ""
        val = lambda key: p[key] if p else ""
        body = f"""<main class="page"><section class="hero-card card"><h1>Your candidate profile</h1><p>You decide whether verified HR users can discover you and whether your contact details are visible. Your locally matched resume is still never uploaded.</p></section><div class="section-head"><div><h2>Professional details</h2><span class="sub">Keep this focused and current.</span></div><a class="btn alt" href="/jobs">Browse jobs</a></div>{notice_html}<form class="card auth-card" method="post" action="/profile"><input type="hidden" name="csrf" value="{esc(user['csrf_token'])}"><div class="grid2"><div class="field"><label>Professional headline</label><input maxlength="140" name="headline" value="{esc(val('headline'))}" placeholder="Senior AR Analyst · O2C · SAP"></div><div class="field"><label>Location</label><input maxlength="100" name="location" value="{esc(val('location'))}" placeholder="Chennai"></div></div><div class="grid2"><div class="field"><label>Years of experience</label><input type="number" min="0" max="50" step="0.5" name="experience_years" value="{esc(val('experience_years'))}"></div><div class="field"><label>Notice period</label><input maxlength="80" name="notice_period" value="{esc(val('notice_period'))}" placeholder="30 days"></div></div><div class="field"><label>Core skills</label><input maxlength="500" name="skills" value="{esc(val('skills'))}" placeholder="Order to cash, collections, SAP, Excel"></div><div class="field"><label>Desired roles</label><input maxlength="300" name="desired_roles" value="{esc(val('desired_roles'))}" placeholder="AR Analyst, Credit Controller"></div><div class="grid2"><div class="field"><label>Preferred work mode</label><select name="work_mode"><option value="">Select</option>{''.join(f'<option {"selected" if val("work_mode")==m else ""}>{m}</option>' for m in ("On-site","Hybrid","Remote","Flexible"))}</select></div><div class="field"><label>LinkedIn profile</label><input maxlength="250" name="linkedin_url" value="{esc(val('linkedin_url'))}" placeholder="https://linkedin.com/in/..."></div></div><div class="field"><label>Professional summary</label><textarea maxlength="1500" name="summary" placeholder="Describe your finance scope, strongest outcomes and the opportunity you want.">{esc(val('summary'))}</textarea></div><div class="consent"><label><input type="checkbox" name="actively_looking" value="1" {checked('actively_looking')}> <b>I am actively looking and want my profile shown to approved HR users.</b></label><br><label><input type="checkbox" name="contact_consent" value="1" {checked('contact_consent')}> I consent to approved HR users seeing my account email and phone.</label><p class="fine">Turning either option off removes your profile or contact details from the HR directory immediately.</p></div><button class="btn" style="margin-top:16px">Save profile</button></form></main>"""
        self.send_html(layout("Candidate profile", body, user))

    def save_profile(self, user: sqlite3.Row, form: dict[str, str]) -> None:
        linkedin = form.get("linkedin_url", "")
        try:
            years = float(form["experience_years"]) if form.get("experience_years") else None
            if years is not None and not 0 <= years <= 50: raise ValueError
        except ValueError:
            self.profile_page(user, "Experience must be between 0 and 50 years.")
            return
        if linkedin and not LINKEDIN_RE.match(linkedin):
            self.profile_page(user, "Enter a complete LinkedIn profile URL or leave it blank.")
            return
        fields = {key: form.get(key, "")[:limit] for key, limit in {
            "headline":140,"location":100,"skills":500,"desired_roles":300,
            "notice_period":80,"work_mode":40,"summary":1500,"linkedin_url":250}.items()}
        with connect() as db:
            db.execute("""UPDATE seeker_profiles SET headline=?,location=?,experience_years=?,skills=?,desired_roles=?,notice_period=?,work_mode=?,summary=?,linkedin_url=?,actively_looking=?,contact_consent=?,updated_at=? WHERE user_id=?""",
                (fields["headline"],fields["location"],years,fields["skills"],fields["desired_roles"],fields["notice_period"],fields["work_mode"],fields["summary"],fields["linkedin_url"],1 if form.get("actively_looking") else 0,1 if form.get("contact_consent") else 0,now_iso(),user["id"]))
            db.execute("INSERT INTO audit_log(user_id,event,created_at) VALUES(?,?,?)", (user["id"], "profile_updated", now_iso()))
        self.profile_page(user, "Your profile and visibility choices have been saved.")

    def hr_page(self, user: sqlite3.Row, query: dict[str, list[str]]) -> None:
        if user["status"] != "active":
            body = '<main class="page"><section class="card pending"><span class="badge">VERIFICATION REQUIRED</span><h1>Your HR account is pending approval</h1><p class="sub">Candidate profiles and contacts stay private until TalentTap verifies your hiring identity and company.</p><p class="fine">An administrator can approve this account with <code>python talenttap_server.py --approve-hr your@email.com</code>.</p></section></main>'
            self.send_html(layout("Verification pending", body, user))
            return
        q = (query.get("q") or [""])[0][:100].strip()
        location = (query.get("location") or [""])[0][:80].strip()
        skill = (query.get("skill") or [""])[0][:80].strip()
        clauses = ["u.role='job_seeker'", "u.status='active'", "p.actively_looking=1"]
        params: list[str] = []
        if q:
            clauses.append("(u.full_name LIKE ? OR p.headline LIKE ? OR p.desired_roles LIKE ? OR p.summary LIKE ?)")
            params += [f"%{q}%"] * 4
        if location:
            clauses.append("p.location LIKE ?"); params.append(f"%{location}%")
        if skill:
            clauses.append("p.skills LIKE ?"); params.append(f"%{skill}%")
        sql = f"""SELECT u.full_name,u.email,u.phone,p.* FROM seeker_profiles p JOIN users u ON u.id=p.user_id WHERE {' AND '.join(clauses)} ORDER BY p.updated_at DESC LIMIT 200"""
        with connect() as db:
            profiles = db.execute(sql, params).fetchall()
            total = db.execute("SELECT COUNT(*) FROM seeker_profiles WHERE actively_looking=1").fetchone()[0]
        cards = []
        for p in profiles:
            initials = "".join(part[:1] for part in p["full_name"].split()[:2]).upper()
            chips = "".join(f'<span class="chip">{esc(s.strip())}</span>' for s in p["skills"].split(",")[:8] if s.strip())
            contact = ""
            if p["contact_consent"]:
                contact = f'<div class="contact"><a href="mailto:{quote(p["email"], safe="@.+-")}">{esc(p["email"])}</a>{f"<a href=\"tel:{esc(p['phone'])}\">{esc(p['phone'])}</a>" if p["phone"] else ""}</div>'
            else:
                contact = '<div class="contact"><span class="fine">Contact sharing is off. Use the LinkedIn profile if supplied.</span></div>'
            linkedin = f'<a href="{esc(p["linkedin_url"])}" target="_blank" rel="noopener">LinkedIn ↗</a>' if p["linkedin_url"] else ""
            cards.append(f"""<article class="card profile"><div class="profile-top"><span class="avatar">{esc(initials)}</span><div class="identity"><h3>{esc(p['full_name'])}</h3><p>{esc(p['headline'] or 'Finance professional')}</p><p>{esc(p['location'])}{' · '+esc(p['experience_years'])+' years' if p['experience_years'] is not None else ''}</p></div><span class="badge">Actively looking</span></div><div class="chips">{chips}</div><p class="summary">{esc(p['summary'] or p['desired_roles'] or 'Profile details available on request.')}</p><p class="fine"><b>Target roles:</b> {esc(p['desired_roles'] or 'Not specified')} · <b>Notice:</b> {esc(p['notice_period'] or 'Ask candidate')} · <b>Mode:</b> {esc(p['work_mode'] or 'Flexible')}</p>{linkedin}{contact}</article>""")
        query_values = {"q":q,"location":location,"skill":skill}
        body = f"""<main class="page"><section class="hero-card card"><h1>Active finance talent</h1><p>Only profiles from job seekers who opted in are shown. Contact details appear only with separate candidate consent.</p><div class="stats"><span class="stat"><b>{total}</b>active profiles</span><span class="stat"><b>{len(profiles)}</b>matching now</span><span class="stat"><b>Verified</b>HR access</span></div></section><div class="section-head"><div><h2>Candidate directory</h2><span class="sub">Search evidence, location and target role.</span></div></div><form class="card filters" method="get" action="/hr"><input class="search" name="q" value="{esc(q)}" placeholder="Name, headline or target role"><input class="search" name="skill" value="{esc(skill)}" placeholder="Skill e.g. SAP"><input class="search" name="location" value="{esc(location)}" placeholder="Location"><button class="btn">Search</button></form><section class="profiles" style="margin-top:14px">{''.join(cards) if cards else '<div class="card empty"><h3>No matching active profiles</h3><p class="sub">Try a broader search or check again as candidates opt in.</p></div>'}</section></main>"""
        self.send_html(layout("Candidate directory", body, user))


class TalentTapHTTPServer(ThreadingHTTPServer):
    """HTTP server with one rolling, consistent database backup per day."""

    daemon_threads = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self._backup_day = ""

    def service_actions(self) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day == self._backup_day:
            return
        try:
            backup_database()
            self._backup_day = day
        except OSError as exc:
            print(f"[{now_iso()}] backup failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="TalentTap role-based account server")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--serve", action="store_true", help="run the HTTP service (default)")
    group.add_argument("--init-db", action="store_true", help="create/update the database schema")
    group.add_argument("--backup", action="store_true", help="create a consistent SQLite backup")
    group.add_argument("--approve-hr", metavar="EMAIL", help="approve a verified HR account")
    args = parser.parse_args()
    init_db()
    if args.init_db:
        print(f"Database ready: {DB_PATH}")
        return 0
    if args.backup:
        print(f"Backup created: {backup_database()}")
        return 0
    if args.approve_hr:
        print("HR account approved." if approve_hr(args.approve_hr) else "No pending HR account found.")
        return 0
    host = os.environ.get("TALENTTAP_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = TalentTapHTTPServer((host, port), TalentTapHandler)
    print(f"TalentTap server: http://{host}:{port}")
    print("Use TALENTTAP_SECURE_COOKIE=0 only for local HTTP development.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
