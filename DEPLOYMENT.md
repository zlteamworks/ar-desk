# Deploying protected TalentTap

## Recommended free production path

Use the website-native Cloudflare Worker + D1 package in
[`cloudflare/README.md`](cloudflare/README.md). It provides real email/password
accounts, server-side role enforcement, recruiter approval,
candidate consent controls and a database backup/recovery path without exposing
the job data through a public static URL.

The Python server described below remains useful for local/offline operation or
a conventional paid container host.

## Why GitHub Pages is no longer sufficient

GitHub Pages can publish `site/index.html`, but it cannot run authentication,
keep sessions, write candidate profiles, approve HR accounts or maintain a
private database. JavaScript-only login would expose all data in the page
source and is not an access control.

Deploy `talenttap_server.py` to a Python/container host with:

- HTTPS at the public edge;
- a persistent encrypted volume mounted for `data/` and `backups/`;
- `TALENTTAP_SECURE_COOKIE=1` (the default);
- a private, independently backed-up copy of the persistent volume;
- restricted operator access for `--approve-hr` and restore operations.

Do not deploy the public `site/` directory separately once account restriction
is required. The server is the gate that serves the generated job page only to
authenticated job seekers.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8000` | Listening port supplied by most cloud hosts |
| `TALENTTAP_HOST` | `127.0.0.1` | Use `0.0.0.0` inside a container |
| `TALENTTAP_DB` | `data/talenttap.sqlite3` | Database path on persistent storage |
| `TALENTTAP_BACKUP_DIR` | `backups/` | Rolling database-backup directory |
| `TALENTTAP_SECURE_COOKIE` | `1` | Keep enabled in every HTTPS environment |

## Operations

```sh
python build_site.py
python talenttap_server.py --init-db
python talenttap_server.py --approve-hr recruiter@company.com
python talenttap_server.py --backup
python talenttap_server.py --serve
```

The SQLite backup API creates a transactionally consistent snapshot. The
service makes one daily snapshot and retains 30. Test restoration regularly;
a backup that has never been restored is not yet proven.

Before accepting real users, publish Terms, Privacy and candidate data-
deletion instructions, choose a retention period, verify recruiters manually,
and configure monitoring. Email verification and password reset need an email
provider and are deliberately not faked by the local implementation.
