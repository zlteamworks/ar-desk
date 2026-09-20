# TalentTap free website deployment

This is one website, not a separate user app:

- TalentTap itself provides registration and login pages.
- The Cloudflare Worker is the website's private server-side backend.
- D1 is the website's private users/profile database.
- The existing job board is served only after a job seeker signs in.
- Approved HR accounts see only candidates who enabled discovery; contacts
  require a second, separate candidate consent.

Passwords never leave the TalentTap backend. They are stored as salted
PBKDF2-SHA256 hashes, optionally strengthened with a server-only pepper.
Sessions are random, hashed in the database, HTTP-only and HTTPS-only.

## One-time free hosting setup

Create or sign into one free Cloudflare account, then authorize Wrangler:

```powershell
wrangler login
cd cloudflare
wrangler d1 create talenttap-users
```

Copy the returned database ID into `wrangler.toml`. Create a random server-only
pepper, initialize the database and deploy:

```powershell
wrangler secret put AUTH_PEPPER
wrangler d1 execute talenttap-users --remote --file schema.sql
wrangler deploy
```

Cloudflare is infrastructure only. Candidates and recruiters do not create a
Cloudflare account, install anything, or leave TalentTap to register or log in.

## Approve an HR account

Verify the recruiter independently, then run:

```powershell
wrangler d1 execute talenttap-users --remote --command "UPDATE users SET status='active' WHERE email='verified@company.com' AND role='hr' AND status='pending_hr'"
```

Never automate approval from the role selected at registration. Otherwise any
visitor could identify as HR and read candidate contacts.

## Refresh jobs

Run the existing collector and builder, then `wrangler deploy`. Static assets
are uploaded with the Worker; the Worker intercepts every route and blocks
direct access to `index.html` and embedded job data.

## Recovery

D1 Free includes Time Travel recovery. Use the Cloudflare dashboard or
`wrangler d1 time-travel` commands to inspect or restore. For longer retention,
periodically export an encrypted copy to an offline drive.
