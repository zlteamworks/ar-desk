const SESSION_DAYS = 7;
// Cloudflare WebCrypto currently caps PBKDF2 at 100,000 iterations. The
// server-only random pepper adds protection beyond each account's unique salt.
const PASSWORD_ITERATIONS = 100000;
const enc = new TextEncoder();

export default {
  async fetch(request, env) {
    try {
      return await route(request, env);
    } catch (error) {
      console.error(error && error.stack ? error.stack : error);
      return htmlPage("Something went wrong", `<main class="page empty"><h1>Something went wrong</h1><p>Please try again.</p></main>`, null, 500);
    }
  }
};

async function route(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/$/, "") || "/";
  const user = await currentUser(request, env);

  if (path === "/health") return new Response("ok", { headers: securityHeaders("text/plain; charset=utf-8") });
  if (request.method === "GET" && path === "/") return redirect(user ? homeFor(user) : "/login");
  if (request.method === "GET" && (path === "/login" || path === "/register")) {
    return user ? redirect(homeFor(user)) : authPage(path.slice(1), env);
  }
  if (request.method === "POST" && path === "/api/register") return register(request, env);
  if (request.method === "POST" && path === "/api/session") return createSession(request, env);
  if (request.method === "POST" && path === "/api/logout") return logout(request, env, user);

  if (!user) return redirect("/login");
  if (request.method === "GET" && path === "/jobs") {
    if (user.role !== "job_seeker") return redirect(homeFor(user));
    return jobPage(request, env, user);
  }
  if (request.method === "GET" && path === "/profile") {
    if (user.role !== "job_seeker") return redirect(homeFor(user));
    return candidateProfile(env, user);
  }
  if (request.method === "POST" && path === "/api/profile") {
    if (user.role !== "job_seeker") return forbidden();
    return saveCandidateProfile(request, env, user);
  }
  if (request.method === "GET" && path === "/hr") {
    if (user.role !== "hr") return redirect(homeFor(user));
    return hrDirectory(url, env, user);
  }

  // Never expose index.html or embedded job data by bypassing authentication.
  if (path === "/index.html" || path.endsWith(".json")) return user && user.role === "job_seeker" ? redirect("/jobs") : forbidden();
  return new Response("Not found", { status: 404, headers: securityHeaders("text/plain; charset=utf-8") });
}

function now() { return new Date().toISOString(); }
function homeFor(user) { return user.role === "hr" ? "/hr" : "/jobs"; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function redirect(location, cookie) {
  const headers = new Headers({ Location: location, "Cache-Control": "no-store" });
  if (cookie) headers.append("Set-Cookie", cookie);
  return new Response(null, { status: 303, headers });
}
function securityHeaders(type = "text/html; charset=utf-8") {
  return {
    "Content-Type": type,
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; worker-src 'self' blob: https://cdnjs.cloudflare.com; connect-src 'self' https://*.goatcounter.com; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
  };
}
function forbidden() { return new Response("Forbidden", { status: 403, headers: securityHeaders("text/plain; charset=utf-8") }); }
function cookieValue(request, name) {
  const cookie = request.headers.get("Cookie") || "";
  for (const part of cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return "";
}
function randomToken(bytes = 32) {
  const data = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode(...data)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", enc.encode(value));
  return [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, "0")).join("");
}

async function currentUser(request, env) {
  const token = cookieValue(request, "tt_session");
  if (!token) return null;
  return env.DB.prepare(`SELECT u.*,s.csrf_token FROM sessions s JOIN users u ON u.user_id=s.user_id WHERE s.token_hash=? AND s.expires_at>?`)
    .bind(await sha256(token), now()).first();
}

async function jsonBody(request) {
  if (!(request.headers.get("Content-Type") || "").includes("application/json")) throw new Error("JSON required");
  return request.json();
}
function normalizeEmail(value) { return String(value || "").trim().toLowerCase(); }
function bytesToBase64(bytes) { return btoa(String.fromCharCode(...bytes)); }
function base64ToBytes(value) { return Uint8Array.from(atob(value), c => c.charCodeAt(0)); }
function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a[i] ^ b[i];
  return difference === 0;
}
async function passwordDigest(password, salt, iterations, pepper = "") {
  const material = await crypto.subtle.importKey("raw", enc.encode(password + pepper), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", salt, iterations, hash: "SHA-256" }, material, 256);
  return new Uint8Array(bits);
}
async function loginAttemptKey(request, email) {
  const ip = request.headers.get("CF-Connecting-IP") || "local";
  return sha256(ip + "|" + email);
}
async function recordLoginFailure(env, key, current) {
  const failures = (current?.failures || 0) + 1;
  const blocked = failures >= 5 ? new Date(Date.now() + 15 * 60000).toISOString() : null;
  await env.DB.prepare(`INSERT INTO auth_attempts(attempt_key,failures,blocked_until,updated_at) VALUES(?,?,?,?) ON CONFLICT(attempt_key) DO UPDATE SET failures=excluded.failures,blocked_until=excluded.blocked_until,updated_at=excluded.updated_at`)
    .bind(key, failures, blocked, now()).run();
}
async function register(request, env) {
  let data;
  try { data = await jsonBody(request); }
  catch { return Response.json({ error: "Unable to read the new account." }, { status: 400, headers: securityHeaders("application/json") }); }
  const role = data.role === "hr" ? "hr" : data.role === "job_seeker" ? "job_seeker" : "";
  const email = normalizeEmail(data.email);
  const password = String(data.password || "");
  const name = String(data.fullName || "").trim().slice(0, 100);
  const phone = String(data.phone || "").trim().slice(0, 18);
  const company = String(data.company || "").trim().slice(0, 120);
  const designation = String(data.designation || "").trim().slice(0, 100);
  const validEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) && email.length <= 254;
  const validPhone = !phone || /^\+?[0-9][0-9 ()-]{6,17}$/.test(phone);
  const validPassword = password.length >= 10 && password.length <= 128 && /[a-z]/.test(password) && /[A-Z]/.test(password) && /\d/.test(password);
  if (!role || !name || !validEmail || !validPhone || !validPassword || (role === "hr" && (!company || !designation))) return Response.json({ error: "Use complete details and a 10+ character password with upper, lower and number." }, { status: 400, headers: securityHeaders("application/json") });
  const userId = randomToken(18), salt = crypto.getRandomValues(new Uint8Array(16));
  const digest = await passwordDigest(password, salt, PASSWORD_ITERATIONS, env.AUTH_PEPPER || "");
  const status = role === "hr" ? "pending_hr" : "active";
  try {
    await env.DB.batch([
      env.DB.prepare(`INSERT INTO users(user_id,email,password_salt,password_hash,password_iterations,role,status,full_name,phone,company,designation,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`)
        .bind(userId, email, bytesToBase64(salt), bytesToBase64(digest), PASSWORD_ITERATIONS, role, status, name, phone, company, designation, now()),
      ...(role === "job_seeker" ? [env.DB.prepare(`INSERT INTO candidate_profiles(user_id,updated_at) VALUES(?,?)`).bind(userId, now())] : []),
      env.DB.prepare(`INSERT INTO audit_log(user_id,event,created_at) VALUES(?,?,?)`).bind(userId, "registered", now())
    ]);
  } catch (error) {
    if (String(error).includes("UNIQUE")) return Response.json({ error: "This account is already registered." }, { status: 409, headers: securityHeaders("application/json") });
    throw error;
  }
  return Response.json({ ok: true }, { headers: securityHeaders("application/json") });
}

async function createSession(request, env) {
  let data;
  try { data = await jsonBody(request); }
  catch { return Response.json({ error: "Sign-in verification failed." }, { status: 401, headers: securityHeaders("application/json") }); }
  const email = normalizeEmail(data.email), password = String(data.password || "");
  const attemptKey = await loginAttemptKey(request, email);
  const attempt = await env.DB.prepare(`SELECT * FROM auth_attempts WHERE attempt_key=?`).bind(attemptKey).first();
  if (attempt?.blocked_until && attempt.blocked_until > now()) return Response.json({ error: "Too many attempts. Try again in 15 minutes." }, { status: 429, headers: securityHeaders("application/json") });
  const user = await env.DB.prepare(`SELECT * FROM users WHERE email=?`).bind(email).first();
  let valid = false;
  if (user && password.length <= 128) {
    const digest = await passwordDigest(password, base64ToBytes(user.password_salt), user.password_iterations, env.AUTH_PEPPER || "");
    valid = safeEqual(digest, base64ToBytes(user.password_hash));
  } else {
    await passwordDigest(password || "invalid", new Uint8Array(16), PASSWORD_ITERATIONS, env.AUTH_PEPPER || "");
  }
  if (!valid || user.status === "suspended") {
    await recordLoginFailure(env, attemptKey, attempt);
    return Response.json({ error: "Email or password is incorrect." }, { status: 401, headers: securityHeaders("application/json") });
  }
  const token = randomToken(), csrf = randomToken(24);
  const expires = new Date(Date.now() + SESSION_DAYS * 86400000).toISOString();
  await env.DB.batch([
    env.DB.prepare(`DELETE FROM sessions WHERE expires_at<?`).bind(now()),
    env.DB.prepare(`DELETE FROM auth_attempts WHERE attempt_key=?`).bind(attemptKey),
    env.DB.prepare(`INSERT INTO sessions(token_hash,user_id,csrf_token,created_at,expires_at) VALUES(?,?,?,?,?)`).bind(await sha256(token), user.user_id, csrf, now(), expires),
    env.DB.prepare(`UPDATE users SET last_login_at=? WHERE user_id=?`).bind(now(), user.user_id),
    env.DB.prepare(`INSERT INTO audit_log(user_id,event,created_at) VALUES(?,?,?)`).bind(user.user_id, "login", now())
  ]);
  const headers = new Headers(securityHeaders("application/json"));
  headers.append("Set-Cookie", `tt_session=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_DAYS * 86400}`);
  return new Response(JSON.stringify({ ok: true, redirect: user.role === "hr" ? "/hr" : "/jobs" }), { headers });
}

async function logout(request, env, user) {
  if (!user) return redirect("/login");
  const form = await request.formData();
  if (String(form.get("csrf") || "") !== user.csrf_token) return forbidden();
  const token = cookieValue(request, "tt_session");
  await env.DB.prepare(`DELETE FROM sessions WHERE token_hash=?`).bind(await sha256(token)).run();
  return redirect("/login", "tt_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0");
}

async function jobPage(request, env, user) {
  const assetUrl = new URL("/index.html", request.url);
  const asset = await env.ASSETS.fetch(new Request(assetUrl));
  let body = await asset.text();
  const bar = `<div style="position:sticky;top:0;z-index:100;background:#101d36;color:#fff;padding:9px 20px;display:flex;justify-content:space-between;align-items:center;font:13px Arial"><span>Signed in as <b>${escapeHtml(user.full_name)}</b> · Job seeker</span><span><a href="/profile" style="color:#8fe8d9;margin-right:16px">My candidate profile</a><form style="display:inline" method="post" action="/api/logout"><input type="hidden" name="csrf" value="${escapeHtml(user.csrf_token)}"><button style="color:#fff;background:transparent;border:1px solid #ffffff55;border-radius:6px;padding:5px 9px">Sign out</button></form></span></div>`;
  body = body.replace("<body>", "<body>" + bar);
  return new Response(body, { headers: securityHeaders() });
}

const CSS = `:root{--navy:#101d36;--navy2:#263c6a;--teal:#0d8b7b;--mint:#8fe8d9;--paper:#f4f7fb;--ink:#132238;--muted:#687b91;--line:#dce4ed}*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#eef4fa,#f7f9fc);color:var(--ink);font:14px/1.5 Arial;min-height:100vh}a{color:var(--teal)}.nav{height:72px;padding:0 max(20px,calc((100vw - 1180px)/2));display:flex;align-items:center;background:linear-gradient(135deg,var(--navy),var(--navy2));color:#fff}.logo{font-size:23px;font-weight:900;color:#fff;text-decoration:none}.links{margin-left:auto;display:flex;gap:12px;align-items:center}.links a,.links button{color:#dce7f5;background:none;border:0;font-weight:700;text-decoration:none;cursor:pointer}.page{width:min(1180px,calc(100% - 30px));margin:34px auto 70px}.auth{display:grid;grid-template-columns:1fr 470px;gap:65px;align-items:center;min-height:calc(100vh - 140px)}.pitch h1{font-size:clamp(42px,6vw,70px);line-height:1;letter-spacing:-.055em;margin:0}.pitch h1 span{color:var(--teal)}.pitch p,.sub{color:var(--muted)}.card{background:#fff;border:1px solid var(--line);border-radius:18px;box-shadow:0 18px 50px #1c2d4a16;padding:28px}.field{margin:13px 0}.field label{display:block;font-size:12px;font-weight:800;margin-bottom:5px}.field input,.field textarea,.field select,.search{width:100%;border:1px solid var(--line);border-radius:10px;padding:11px 12px;font:14px Arial}.field textarea{min-height:100px}.grid2,.roles{display:grid;grid-template-columns:1fr 1fr;gap:11px}.role input{position:absolute;opacity:0}.role label{display:block;border:1px solid var(--line);border-radius:11px;padding:13px;cursor:pointer}.role input:checked+label{border-color:var(--teal);background:#effbf8;box-shadow:0 0 0 3px #0d8b7b17}.role b,.role small{display:block}.role small{color:var(--muted)}.btn{border:0;border-radius:10px;padding:11px 17px;background:linear-gradient(135deg,var(--teal),#087367);color:#fff;font-weight:800;cursor:pointer;text-decoration:none}.btn.full{width:100%}.error,.notice{padding:10px 12px;border-radius:9px;margin:12px 0}.error{color:#a52f3d;background:#fce8eb}.notice{color:#146b4c;background:#e4f5ef}.hero{color:#fff;background:linear-gradient(135deg,var(--navy),#493fa4)}.hero p{color:#dce2f2}.filters{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:9px;margin:20px 0}.profiles{display:grid;grid-template-columns:1fr 1fr;gap:13px}.profile h3{margin:0}.badge,.chip{display:inline-block;border-radius:999px;padding:4px 8px;font-size:11px;font-weight:800;background:#e0f4ea;color:#16845b}.chips{display:flex;gap:5px;flex-wrap:wrap;margin:12px 0}.chip{background:#eff3f8;color:#41536b}.contact{display:flex;gap:12px;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:12px}.consent{padding:13px;border:1px solid #cce9e3;background:#effbf8;border-radius:11px}.empty{text-align:center;margin:70px auto}.hidden{display:none}@media(max-width:800px){.auth{grid-template-columns:1fr;gap:20px}.profiles{grid-template-columns:1fr}.filters,.grid2,.roles{grid-template-columns:1fr}}`;

function nav(user) {
  if (!user) return `<div class="nav"><a class="logo" href="/">TalentTap</a><div class="links"><a href="/login">Sign in</a><a href="/register">Create account</a></div></div>`;
  const links = user.role === "hr" ? `<a href="/hr">Candidates</a>` : `<a href="/jobs">Jobs</a><a href="/profile">My profile</a>`;
  return `<div class="nav"><a class="logo" href="/">TalentTap</a><div class="links">${links}<form method="post" action="/api/logout"><input type="hidden" name="csrf" value="${escapeHtml(user.csrf_token)}"><button>Sign out</button></form></div></div>`;
}
function htmlPage(title, content, user = null, status = 200) {
  return new Response(`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)} · TalentTap</title><style>${CSS}</style><body>${nav(user)}${content}</body></html>`, { status, headers: securityHeaders() });
}

function authPage(mode) {
  const registerMode = mode === "register";
  const extra = registerMode ? `<div class="field"><label>I am joining as</label><div class="roles"><div class="role"><input id="seek" type="radio" name="role" value="job_seeker" checked><label for="seek"><b>Job seeker</b><small>Find roles and become discoverable.</small></label></div><div class="role"><input id="hire" type="radio" name="role" value="hr"><label for="hire"><b>HR / Recruiter</b><small>Find active finance candidates after approval.</small></label></div></div></div><div class="grid2"><div class="field"><label>Full name</label><input required maxlength="100" name="fullName"></div><div class="field"><label>Phone</label><input maxlength="18" name="phone"></div></div><div class="grid2"><div class="field"><label>Company (HR)</label><input maxlength="120" name="company"></div><div class="field"><label>Designation (HR)</label><input maxlength="100" name="designation"></div></div>` : "";
  const body = `<main class="page auth"><section class="pitch"><span class="badge">FINANCE TALENT, CONNECTED</span><h1>One website.<br><span>Two clear paths.</span></h1><p>Private candidate profiles, approved recruiter access and relevant finance opportunities—all within TalentTap.</p></section><section class="card"><h2>${registerMode ? "Create your account" : "Welcome back"}</h2><p class="sub">${registerMode ? "Choose the workspace built for you." : "Sign in to continue."}</p><div id="message"></div><form id="auth-form">${extra}<div class="field"><label>Email</label><input required type="email" maxlength="254" name="email" autocomplete="email"></div><div class="field"><label>Password</label><input required type="password" minlength="10" maxlength="128" name="password" autocomplete="${registerMode ? "new-password" : "current-password"}"></div>${registerMode ? '<p class="sub">Use at least 10 characters with uppercase, lowercase and a number.</p>' : ""}<button class="btn full">${registerMode ? "Create account" : "Sign in"}</button></form><p class="sub">${registerMode ? 'Already registered? <a href="/login">Sign in</a>' : 'New here? <a href="/register">Create an account</a>'}</p></section></main>`;
  const script = `<script>const form=document.getElementById('auth-form'),msg=document.getElementById('message');const show=(text,bad=true)=>msg.innerHTML='<div class="'+(bad?'error':'notice')+'">'+String(text).replace(/[&<>]/g,'')+'</div>';form.addEventListener('submit',async e=>{e.preventDefault();const button=form.querySelector('button');button.disabled=true;const data=Object.fromEntries(new FormData(form));try{const res=await fetch('${registerMode ? "/api/register" : "/api/session"}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),out=await res.json();if(!res.ok)throw new Error(out.error);${registerMode ? `show('Account created. You can sign in now.',false);form.reset();setTimeout(()=>location.href='/login',900);` : `location.href=out.redirect;`}}catch(err){show(err.message||'Please try again.')}finally{button.disabled=false}});</script>`;
  return new Response(`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${registerMode ? "Create account" : "Sign in"} · TalentTap</title><style>${CSS}</style><body>${nav(null)}${body}${script}</body></html>`, { headers: securityHeaders() });
}

async function candidateProfile(env, user, message = "") {
  const p = await env.DB.prepare(`SELECT * FROM candidate_profiles WHERE user_id=?`).bind(user.user_id).first() || {};
  const val = key => escapeHtml(p[key] ?? "");
  const checked = key => p[key] ? "checked" : "";
  const content = `<main class="page"><section class="card hero"><h1>Your candidate profile</h1><p>You decide whether approved HR users can discover you and see your contact details. Resumes used for matching still remain in your browser.</p></section>${message ? `<div class="notice">${escapeHtml(message)}</div>` : ""}<h2>Professional details</h2><form class="card" method="post" action="/api/profile"><input type="hidden" name="csrf" value="${escapeHtml(user.csrf_token)}"><div class="grid2"><div class="field"><label>Professional headline</label><input maxlength="140" name="headline" value="${val("headline")}"></div><div class="field"><label>Location</label><input maxlength="100" name="location" value="${val("location")}"></div></div><div class="grid2"><div class="field"><label>Years of experience</label><input type="number" min="0" max="50" step=".5" name="experience_years" value="${val("experience_years")}"></div><div class="field"><label>Notice period</label><input maxlength="80" name="notice_period" value="${val("notice_period")}"></div></div><div class="field"><label>Core skills</label><input maxlength="500" name="skills" value="${val("skills")}"></div><div class="field"><label>Desired roles</label><input maxlength="300" name="desired_roles" value="${val("desired_roles")}"></div><div class="grid2"><div class="field"><label>Work mode</label><select name="work_mode">${["","On-site","Hybrid","Remote","Flexible"].map(x=>`<option ${p.work_mode===x?"selected":""}>${x||"Select"}</option>`).join("")}</select></div><div class="field"><label>LinkedIn URL</label><input maxlength="250" name="linkedin_url" value="${val("linkedin_url")}"></div></div><div class="field"><label>Professional summary</label><textarea maxlength="1500" name="summary">${val("summary")}</textarea></div><div class="consent"><label><input type="checkbox" name="actively_looking" value="1" ${checked("actively_looking")}> <b>Show my profile to approved HR users.</b></label><br><label><input type="checkbox" name="contact_consent" value="1" ${checked("contact_consent")}> Allow approved HR users to see my email and phone.</label></div><button class="btn" style="margin-top:15px">Save profile</button></form></main>`;
  return htmlPage("Candidate profile", content, user);
}
async function saveCandidateProfile(request, env, user) {
  const form = await request.formData();
  if (String(form.get("csrf") || "") !== user.csrf_token) return forbidden();
  const text = (key, max) => String(form.get(key) || "").trim().slice(0, max);
  const yearsText = text("experience_years", 6), years = yearsText === "" ? null : Number(yearsText);
  if (years !== null && (!Number.isFinite(years) || years < 0 || years > 50)) return candidateProfile(env, user, "Experience must be between 0 and 50 years.");
  const linkedin = text("linkedin_url", 250);
  if (linkedin && !/^https:\/\/(?:[a-z]{2,3}\.)?linkedin\.com\/in\/[A-Za-z0-9._%-]+\/?$/i.test(linkedin)) return candidateProfile(env, user, "Enter a complete LinkedIn profile URL.");
  await env.DB.prepare(`UPDATE candidate_profiles SET headline=?,location=?,experience_years=?,skills=?,desired_roles=?,notice_period=?,work_mode=?,summary=?,linkedin_url=?,actively_looking=?,contact_consent=?,updated_at=? WHERE user_id=?`).bind(text("headline",140),text("location",100),years,text("skills",500),text("desired_roles",300),text("notice_period",80),text("work_mode",40),text("summary",1500),linkedin,form.get("actively_looking")?1:0,form.get("contact_consent")?1:0,now(),user.user_id).run();
  await env.DB.prepare(`INSERT INTO audit_log(user_id,event,created_at) VALUES(?,?,?)`).bind(user.user_id,"profile_updated",now()).run();
  return candidateProfile(env, user, "Your profile and privacy choices were saved.");
}

async function hrDirectory(url, env, user) {
  if (user.status !== "active") return htmlPage("Verification pending", `<main class="page empty card"><span class="badge">VERIFICATION REQUIRED</span><h1>Your HR account is pending approval</h1><p class="sub">Candidate profiles and contacts remain private until TalentTap verifies your company and hiring role.</p></main>`, user);
  const q = (url.searchParams.get("q") || "").slice(0, 80), skill = (url.searchParams.get("skill") || "").slice(0, 80), location = (url.searchParams.get("location") || "").slice(0, 80);
  let sql = `SELECT u.full_name,u.email,u.phone,p.* FROM candidate_profiles p JOIN users u ON u.user_id=p.user_id WHERE u.role='job_seeker' AND u.status='active' AND p.actively_looking=1`, params = [];
  if (q) { sql += ` AND (u.full_name LIKE ? OR p.headline LIKE ? OR p.desired_roles LIKE ?)`; params.push(...Array(3).fill(`%${q}%`)); }
  if (skill) { sql += ` AND p.skills LIKE ?`; params.push(`%${skill}%`); }
  if (location) { sql += ` AND p.location LIKE ?`; params.push(`%${location}%`); }
  sql += ` ORDER BY p.updated_at DESC LIMIT 200`;
  const { results = [] } = await env.DB.prepare(sql).bind(...params).all();
  const cards = results.map(p => { const chips=p.skills.split(",").slice(0,8).filter(Boolean).map(x=>`<span class="chip">${escapeHtml(x.trim())}</span>`).join(""); const contact=p.contact_consent?`<div class="contact"><a href="mailto:${encodeURIComponent(p.email)}">${escapeHtml(p.email)}</a>${p.phone?`<a href="tel:${escapeHtml(p.phone)}">${escapeHtml(p.phone)}</a>`:""}</div>`:`<div class="contact sub">Contact sharing is off.</div>`; return `<article class="card profile"><span class="badge">Actively looking</span><h3>${escapeHtml(p.full_name)}</h3><p class="sub">${escapeHtml(p.headline||"Finance professional")} · ${escapeHtml(p.location)}</p><div class="chips">${chips}</div><p>${escapeHtml(p.summary||p.desired_roles||"Profile details available on request.")}</p><p class="sub"><b>Target:</b> ${escapeHtml(p.desired_roles||"Not specified")} · <b>Notice:</b> ${escapeHtml(p.notice_period||"Ask candidate")}</p>${p.linkedin_url?`<a href="${escapeHtml(p.linkedin_url)}" target="_blank" rel="noopener">LinkedIn ↗</a>`:""}${contact}</article>`; }).join("");
  const content = `<main class="page"><section class="card hero"><h1>Active finance talent</h1><p>Only opted-in candidates appear. Contact details require separate consent.</p></section><form class="card filters"><input class="search" name="q" value="${escapeHtml(q)}" placeholder="Name or target role"><input class="search" name="skill" value="${escapeHtml(skill)}" placeholder="Skill"><input class="search" name="location" value="${escapeHtml(location)}" placeholder="Location"><button class="btn">Search</button></form><section class="profiles">${cards||'<div class="card empty"><h3>No matching active profiles</h3></div>'}</section></main>`;
  return htmlPage("Candidate directory", content, user);
}
