"""Find hiring-manager / recruiter "we're hiring" posts on LinkedIn that the scraper can't see.

The scraper (scraper.py) reads LinkedIn *job listings* logged out, so jobs that only exist as a post
- e.g. Prove Expertise's Software Tester opening in Oulu, posted by the hiring manager - never reach
jobs.json. This tool searches LinkedIn *posts* (Content search, past week/month, newest first)
through the logged-in "Priya Automation Profile" Chrome on CDP port 9222, keeps posts that look like
a hiring announcement for one of Priya's roles in Finland or remote, and prints them as candidates.
Read-only: it never likes, comments, connects or messages.

Usage:
    python linkedin_hiring_posts.py scan [--period past-week|past-month] [--all]
        Prints new candidate posts as JSON lines and records them in scratch/linkedin_posts_seen.json
        (--all: also print posts already seen).
    python linkedin_hiring_posts.py add --title T --company C --location L --poster NAME
            --profile URL --text-file FILE [--apply-email E] [--apply-url U]
        Adds the chosen post to jobs.json as a manual job (source "linkedin_post", eval_model
        "manual", matches_requirements "yes"), writes its job_descriptions/ file and prints the id.
        Commit and push jobs.json afterwards; hand-added entries must be re-checked after the
        scraper's next push (see memory.md).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN = os.path.join(HERE, "scratch", "linkedin_posts_seen.json")
JOBS = os.path.join(HERE, "jobs.json")

# Searches: Priya's role families x Finland/Oulu, English and Finnish hiring phrasing.
QUERIES = [
    "hiring Oulu", "Oulu #hiring", "rekrytoimme Oulu", "etsimme Oulu", "haemme Oulu",
    "Oulu test engineer", "Oulu software tester", "ohjelmistotestaaja", "testaaja Oulu",
    "Oulu QA", "Oulu DevOps", "Oulu release", "Oulu quality engineer",
    "hiring software tester Finland", "hiring test engineer Finland", "hiring DevOps Finland",
    "hiring release manager Finland", "hiring configuration manager Finland",
    "hiring quality engineer Finland", "hiring technical writer Finland",
    "hiring documentation specialist Finland", "etsimme testaajaa", "etsimme DevOps",
]
HIRING = re.compile(r"hiring|we'?re looking for|we are looking for|join (our|the) team|open (position|role)|"
                    r"apply|rekry|etsimme|haemme|palkkaamme|hae (meille|paikkaa)|haku on auki|hakuaika|"
                    r"tule meille|avoin(na)? (työpaikka|tehtävä|paikka)", re.I)
ROLE = re.compile(r"test|tester|testaaj|testau|\bQA\b|quality|laatu|release|devops|ci/cd|configuration|"
                  r"konfiguraatio|documentation|technical writ|dokumentaatio|validation|validoin|"
                  r"requirement|vaatimus|product specialist|project coordinat|delivery", re.I)
PLACE = re.compile(r"oulu|finland|suomi|suomessa|helsinki|espoo|tampere|vantaa|turku|jyväskylä|"
                   r"remote|etätyö|hybrid", re.I)
NOISE = re.compile(r"CAGR|market (size|research|outlook)|visa sponsorship 2026|delivery driver", re.I)
SKIP_LINES = {"Follow", "•", "Like", "Comment", "Repost", "Send", "…more", "...more", "Show translation"}
LINKS_JS = r"""() => [...document.querySelectorAll('a[href*="/in/"], a[href*="/company/"]')]
    .map(a => [a.innerText.trim().split('\n')[0], a.href.split('?')[0]]).filter(x => x[0])"""


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def scan(period, show_all):
    from playwright.sync_api import sync_playwright
    seen = _load(SEEN, {})
    found = 0
    with sync_playwright() as p:
        ctx = p.chromium.connect_over_cdp("http://127.0.0.1:9222").contexts[0]
        pg = ctx.new_page()
        pg.set_default_timeout(10000)
        try:
            for q in QUERIES:
                pg.goto("https://www.linkedin.com/search/results/content/?keywords=" + urllib.parse.quote(q)
                        + f"&datePosted=%22{period}%22&sortBy=%22date_posted%22",
                        wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(6000)
                for _ in range(5):
                    pg.mouse.wheel(0, 4000)
                    pg.wait_for_timeout(1800)
                body = pg.locator("body").inner_text()
                links = pg.evaluate(LINKS_JS)
                for chunk in body.split("Feed post")[1:]:
                    lines = [l.strip() for l in chunk.splitlines() if l.strip() and l.strip() not in SKIP_LINES]
                    if len(lines) < 3:
                        continue
                    name, text = lines[0], " | ".join(lines[1:])[:2500]
                    if NOISE.search(text) or not (HIRING.search(text) and ROLE.search(text) and PLACE.search(text)):
                        continue
                    key = hashlib.md5((name + text[:300]).encode("utf-8")).hexdigest()[:12]
                    new = key not in seen
                    if new:
                        seen[key] = {"name": name, "first_seen": datetime.date.today().isoformat()}
                    if new or show_all:
                        found += 1
                        print(json.dumps({"key": key, "query": q, "poster": name,
                                          "profile": next((h for t, h in links if t == name), ""),
                                          "text": text}, ensure_ascii=False))
        finally:
            pg.close()
    os.makedirs(os.path.dirname(SEEN), exist_ok=True)
    with open(SEEN, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)
    print(f"# {found} candidate post(s)", file=sys.stderr)


def add(a):
    jobs = _load(JOBS, [])
    slug = re.sub(r"[^a-z0-9]+", "-", f"{a.company} {a.title}".lower()).strip("-")[:60]
    url = a.apply_url or f"manual://linkedin-post/{slug}"
    if any(j.get("url") == url for j in jobs):
        sys.exit(f"already in jobs.json: {url}")
    jid = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    text = open(a.text_file, encoding="utf-8").read()
    desc_rel = f"job_descriptions/{slug.replace('-', '_')}_{jid}.txt"
    with open(os.path.join(HERE, desc_rel), "w", encoding="utf-8") as f:
        f.write(f"Title: {a.title}\nCompany: {a.company}\nLocation: {a.location}\nURL: {url}\n"
                f"Source: LinkedIn post by {a.poster} ({a.profile})\n"
                + (f"Apply by email: {a.apply_email}\n" if a.apply_email else "")
                + "\n" + "=" * 40 + "\nJOB DESCRIPTION:\n" + "=" * 40 + "\n" + text + "\n")
    now = datetime.datetime.now().astimezone()
    jobs.append({
        "id": jid, "title": a.title, "company": a.company, "location": a.location, "url": url,
        "posted_date": now.strftime("%Y-%m-%d"), "deadline": "N/A", "added_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "linkedin_post", "description_file": desc_rel, "matches_requirements": "yes",
        "reason": f"From a LinkedIn hiring post by {a.poster} ({a.profile})"
                  + (f"; apply by email to {a.apply_email}" if a.apply_email else "") + ".",
        "eval_model": "manual", "applied": "no",
    })
    with open(JOBS, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2)          # same format as scraper.py, so the diff stays one entry
    print(jid)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--period", default="past-week", choices=["past-24h", "past-week", "past-month"])
    s.add_argument("--all", action="store_true")
    d = sub.add_parser("add")
    for k in ["title", "company", "location", "poster", "profile", "text-file"]:
        d.add_argument("--" + k, required=True)
    d.add_argument("--apply-email")
    d.add_argument("--apply-url")
    args = ap.parse_args()
    scan(args.period, args.all) if args.cmd == "scan" else add(args)
