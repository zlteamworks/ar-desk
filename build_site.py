"""
SITE BUILDER  -  bakes the latest run into the public Job Bot page
==================================================================

    python naukri_job_bot.py     # writes jobs.json
    python build_site.py         # writes site/index.html

The published page is a single self-contained file. It cannot fetch the
job data at view time (browsers block cross-site requests, and the
portals would block them anyway), so the data is embedded directly into
the HTML here, at build time.

That is also why "refreshing the site" means republishing it. The whole
refresh loop is:

    Task Scheduler -> naukri_job_bot.py -> build_site.py -> republish

Nothing about a visitor's resume is involved in any of this: resumes are
read and scored inside the visitor's own browser and never transmitted.
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(SCRIPT_DIR, "site_template.html")
DATA = os.path.join(SCRIPT_DIR, "jobs.json")
OUT_DIR = os.path.join(SCRIPT_DIR, "site")
OUT = os.path.join(OUT_DIR, "index.html")

# The template carries this exact token where the payload belongs.
MARKER = '"__JOBS_PAYLOAD__"'


def load_payload(path=DATA):
    if not os.path.exists(path):
        print(f"  ! {os.path.basename(path)} not found.")
        print("    Run 'python naukri_job_bot.py' first - it writes jobs.json")
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build(payload, template_path=TEMPLATE, out_path=OUT):
    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    if MARKER not in html:
        raise SystemExit(f"Template is missing the {MARKER} marker.")

    # json.dumps output is safe to drop into a <script type="application/json">
    # block except for a literal "</script>" inside a string value, which
    # would close the tag early. Escaping the slash keeps the JSON valid.
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    blob = blob.replace("</", "<\\/")

    html = html.replace(MARKER, blob)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    payload = load_payload()
    if payload is None:
        return 1

    out = build(payload)
    counts = payload.get("counts", {})
    by_source = counts.get("by_source", {})
    size_kb = os.path.getsize(out) // 1024

    print("Job Bot site built")
    print(f"  data from : {payload.get('generated_label', '?')}")
    print(f"  jobs      : {counts.get('total', 0)} "
          f"({counts.get('shortlist', 0)} shortlist, "
          f"{counts.get('near', 0)} near-miss)")
    for src, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"      {src:<10}: {n}")
    print(f"  matchable : {counts.get('with_text', 0)} jobs carry JD text")
    print(f"  -> {out} ({size_kb} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
