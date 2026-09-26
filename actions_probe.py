"""Trial: which scrape targets still return job links when run from a GitHub Actions
(datacenter IP) runner instead of the home PC.

Read-only with respect to the repo: reuses scraper.py's own target list and parsers but
never writes jobs.json / seen_urls.json / checkpoint.json, never calls an LLM, never
touches Firestore or git. Output goes to probe_out/ (uploaded as a workflow artifact)
and to the workflow's step summary.
"""
import json
import os
import re
import time

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

import scraper

OUT_DIR = os.path.join(scraper.BASE_DIR, "probe_out")
SHOTS_DIR = os.path.join(OUT_DIR, "zero_result_screenshots")

# Title/body fingerprints of bot walls, captchas and login walls.
BLOCK_PATTERNS = re.compile(
    r"just a moment|attention required|verify you are human|are you a robot|captcha|"
    r"access denied|security check|unusual traffic|blocked|authwall|sign in to|"
    r"additional verification required|request unsuccessful",
    re.I,
)


def main():
    os.makedirs(SHOTS_DIR, exist_ok=True)
    seen_urls = set()
    try:
        with open(scraper.SEEN_URLS_FILE, encoding="utf-8") as f:
            seen_urls = set(json.load(f))
        with open(scraper.JOBS_FILE, encoding="utf-8") as f:
            seen_urls.update(j["url"] for j in json.load(f))
    except Exception:
        pass

    targets = scraper.generate_targets()
    limit = int(os.environ.get("PROBE_MAX_TARGETS", "0") or 0)
    if limit:
        targets = targets[:limit]
    print(f"Probing {len(targets)} targets...")

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        page.set_default_navigation_timeout(60000)
        page.set_default_timeout(60000)

        for i, target in enumerate(targets, 1):
            row = {"id": target["id"], "platform": target["platform"], "url": target["url"]}
            start = time.monotonic()
            try:
                resp = page.goto(target["url"], timeout=60000)
                row["status"] = resp.status if resp else None
                for _ in range(target.get("scroll_count", scraper._DEFAULT_SCROLL_COUNT)):
                    page.mouse.wheel(0, 2000)
                    time.sleep(1.5)
                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                if target["platform"] == "linkedin":
                    jobs = scraper.parse_linkedin(soup)
                else:
                    jobs = scraper.parse_generic(soup, target["url"])
                row["found"] = len(jobs)
                row["new"] = sum(1 for j in jobs if j["url"] not in seen_urls)
                row["final_url"] = page.url
                row["title"] = (page.title() or "")[:120]
                row["block_hint"] = ""
                if not jobs:
                    # Only meaningful on empty pages - normal LinkedIn results also say "Sign in to".
                    head = row["title"] + " " + soup.get_text(" ", strip=True)[:3000]
                    m = BLOCK_PATTERNS.search(head)
                    row["block_hint"] = m.group(0) if m else ""
                    page.screenshot(path=os.path.join(SHOTS_DIR, f"{target['id']}.png"))
            except Exception as e:
                row["found"] = 0
                row["new"] = 0
                row["error"] = str(e).splitlines()[0][:200]
                try:
                    page.goto("about:blank", timeout=5000)
                except Exception:
                    pass
            row["seconds"] = round(time.monotonic() - start, 1)
            results.append(row)
            print(f"[{i}/{len(targets)}] {row['id']}: found={row['found']} new={row['new']} "
                  f"status={row.get('status')} block={row.get('block_hint', '')!r} {row.get('error', '')}")

        browser.close()

    with open(os.path.join(OUT_DIR, "probe_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    # Per-site rollup (target ids minus keyword slug / page suffix -> site prefix).
    prefixes = [t["id_prefix"] for t in scraper._KEYWORD_SITE_TEMPLATES] + [s["id"] for s in scraper.FIXED_SITES]
    prefixes.sort(key=len, reverse=True)
    sites = {}
    for r in results:
        site = next((p for p in prefixes if r["id"] == p or r["id"].startswith(p + "_")), r["id"])
        s = sites.setdefault(site, {"targets": 0, "nonzero": 0, "found": 0, "new": 0, "blocked": 0, "errors": 0})
        s["targets"] += 1
        s["nonzero"] += 1 if r["found"] else 0
        s["found"] += r["found"]
        s["new"] += r["new"]
        s["blocked"] += 1 if r.get("block_hint") else 0
        s["errors"] += 1 if r.get("error") else 0

    lines = ["## Actions scrape probe - per site", "",
             "| Site | Targets | Targets with links | Links found | Not yet seen | Block hints | Errors |",
             "|---|---|---|---|---|---|---|"]
    for site, s in sites.items():
        lines.append(f"| {site} | {s['targets']} | {s['nonzero']} | {s['found']} | {s['new']} | {s['blocked']} | {s['errors']} |")
    summary = "\n".join(lines) + "\n"
    with open(os.path.join(OUT_DIR, "summary.md"), "w", encoding="utf-8") as f:
        f.write(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(summary)
    print(summary)


if __name__ == "__main__":
    main()
