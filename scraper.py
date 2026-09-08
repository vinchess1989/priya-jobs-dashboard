import json
import time
import os
import sys
import re
import unicodedata
import subprocess
import threading
import hashlib
import argparse
import glob
from datetime import datetime, timedelta
from urllib.parse import urljoin, quote
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import requests
from dotenv import load_dotenv
from filelock import FileLock, Timeout

# Load environment variables from .env file if it exists
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(BASE_DIR, "jobs.json")
SEEN_URLS_FILE = os.path.join(BASE_DIR, "seen_urls.json")
CHECKPOINT_FILE = os.path.join(BASE_DIR, "checkpoint.json")
REQ_FILE = os.path.join(BASE_DIR, "job_requirements.md")
DELETED_FILE = os.path.join(BASE_DIR, "deleted.json")
HISTORY_FILE = os.path.join(BASE_DIR, "jobs_history.json")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
SCRAPER_LOCK_FILE = os.path.join(BASE_DIR, "scraper.lock")
# Shared across the manju_jobs/vineeth_jobs repos (not per-repo like SCRAPER_LOCK_FILE) so the
# two dashboards' always-on scraper daemons take turns doing their scrape+review+push cycle
# instead of both hitting the single-threaded (parallel=1) local LLM server at once.
PIPELINE_LOCK_FILE = os.path.join(os.environ.get("USERPROFILE", ""), ".claude", "scraper_pipeline.lock")
# Priority chain for the shared local LLM server: OpenClaw > manju_jobs > vineeth_jobs >
# priya_jobs (lowest). manju_jobs claims MANJU_PRIORITY_LOCK_FILE and vineeth_jobs claims
# VINEETH_PRIORITY_LOCK_FILE before requesting PIPELINE_LOCK_FILE (see their own scraper.py
# files); priya_jobs only ever defers to both (see _post_llm_with_retry below) since nothing
# is lower priority than it - it has no lock of its own to claim. Real OS-level file locks,
# so if a higher-priority process dies while holding one the OS releases it automatically -
# no stale-flag cleanup needed here.
MANJU_PRIORITY_LOCK_FILE = os.path.join(os.environ.get("USERPROFILE", ""), ".claude", "scraper_manju_priority.lock")
VINEETH_PRIORITY_LOCK_FILE = os.path.join(os.environ.get("USERPROFILE", ""), ".claude", "scraper_vineeth_priority.lock")


class TeeLogger:
    """Mirrors every print() to both the terminal and a daily log file.

    start_capture() / stop_capture() collect lines from one scrape batch.
    Lines from each batch are accumulated in _cycle_buf; flush_cycle() returns
    and clears it so analyze_scrape_run_log() sees the whole cycle at once.
    """

    def __init__(self, log_path: str):
        self._stdout = sys.stdout
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self._log_file = open(log_path, 'a', encoding='utf-8', buffering=1)
        self._capture_buf: list[str] = []
        self._capturing = False
        self._cycle_buf: list[str] = []

    def write(self, msg: str):
        self._stdout.write(msg)
        self._log_file.write(msg)
        if self._capturing:
            self._capture_buf.append(msg)

    def flush(self):
        self._stdout.flush()
        self._log_file.flush()

    def close(self):
        self._log_file.close()

    def start_capture(self):
        self._capture_buf.clear()
        self._capturing = True

    def stop_capture(self):
        """Stop capturing; append this batch's lines to the cycle buffer."""
        self._capturing = False
        lines = ''.join(self._capture_buf).splitlines()
        self._cycle_buf.extend(lines)

    def flush_cycle(self) -> list[str]:
        """Return the full cycle's accumulated lines and reset for the next cycle."""
        lines = list(self._cycle_buf)
        self._cycle_buf.clear()
        return lines

    def __getattr__(self, name):
        return getattr(self._stdout, name)


_tee_logger: TeeLogger | None = None


def _checkpoint_reached_end() -> bool:
    """True if the last scrape_all_jobs() call visited the final target."""
    try:
        with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
            idx = json.load(f).get("target_index", 0)
        return idx >= len(generate_targets())
    except Exception:
        return False

# ─────────────────────────────────────────────────────────────────────────────
# BLOCKED KEYWORDS — jobs with these keywords in the title are auto-deleted
# ─────────────────────────────────────────────────────────────────────────────
BLOCKED_TITLE_KEYWORDS = [
    # C-suite / pure-executive (non-technical) - Removed per user request
    # Non-technical: HR & Recruiting
    'talent acquisition', 'recruiter', 'human resources',
    # Non-technical: Finance
    'financial analyst', 'finance analyst', 'accountant', 'investor relations',
    # Non-technical: Marketing (specific non-engineer roles)
    'marketing manager', 'marketing coordinator', 'marketing analyst',
    'digital marketing', 'content marketing', 'social media manager',
    # Non-technical: Sales management (not "Sales Engineer" or "Technical Account Manager")
    'sales manager', 'sales coordinator', 'inside sales',
    # Non-technical: Supply chain / sourcing / procurement
    'sourcing manager', 'supply chain analyst', 'procurement analyst',
    'supply chain manager',
    # Non-technical: Other
    'venture associate', 'venture partner',
]

# ─────────────────────────────────────────────────────────────────────────────
# JOB SOURCES — DevOps / Release & Configuration Management / Product Specialist
# Scope: Finland (Priya is Oulu-based) + remote-EU
# ─────────────────────────────────────────────────────────────────────────────
# ── Keyword terms — each has an English variant (all Priya sites are English)
_KEYWORD_TERMS = [
    {"en": "DevOps Engineer"},
    {"en": "Configuration Manager"},
    {"en": "Release Manager"},
    {"en": "Product Specialist"},
]

# ── Sites that accept a keyword injected into the URL ─────────────────────
# All Priya keyword sites are English-language platforms.
_KEYWORD_SITE_TEMPLATES = [
    {
        "id_prefix": "linkedin_fi",
        "platform": "linkedin",
        "lang": "en",
        "pages": 3,
        "url_template": "https://www.linkedin.com/jobs/search?keywords={term_enc}&location=Finland&sortBy=DD",
    },
    {
        "id_prefix": "linkedin_eu",
        "platform": "linkedin",
        "lang": "en",
        "pages": 2,
        "url_template": "https://www.linkedin.com/jobs/search?keywords={term_enc}&location=European%20Union&sortBy=DD",
    },
    {
        "id_prefix": "linkedin_eu_remote",
        "platform": "linkedin",
        "lang": "en",
        "pages": 2,
        "url_template": "https://www.linkedin.com/jobs/search?keywords={term_enc}&location=European%20Union&f_WT=2&sortBy=DD",
    },
    {
        "id_prefix": "indeed_fi",
        "platform": "indeed",
        "lang": "en",
        "pages": 3,
        "url_template": "https://fi.indeed.com/jobs?q={term_enc}&l=Finland&sort=date",
    },
    {
        "id_prefix": "indeed_remote",
        "platform": "indeed",
        "lang": "en",
        "pages": 3,
        "url_template": "https://www.indeed.com/jobs?q={term_enc}&l=Remote&sort=date",
    },
    {
        "id_prefix": "jobly_fi",
        "platform": "jobly",
        "lang": "en",
        "pages": 1,
        "url_template": "https://www.jobly.fi/tyopaikat?search={term_enc}",
    },
    {
        "id_prefix": "duunitori",
        "platform": "duunitori",
        "lang": "en",
        "scroll_count": 12,
        "url_template": "https://duunitori.fi/tyopaikat?haku={term_enc}&jarjestys=uusimmat",
    },
    {
        "id_prefix": "kuntarekry",
        "platform": "kuntarekry",
        "lang": "en",
        "scroll_count": 8,
        "url_template": "https://www.kuntarekry.fi/fi/tyopaikat/?sq={term_enc}&sort=-publish_time",
    },
]

# ── Fixed sites (career pages, boards that don't fit a keyword URL template) —
# broad, no-keyword sweeps sorted by newest, matching manju_jobs's site list
# (Duunitori/Indeed/Jobly/Kuntarekry/Työmarkkinatori/MeetFrank), adapted to
# Finland + remote-EU scope instead of Worldwide.
FIXED_SITES = [
    {"id": "linkedin_fi_broad", "platform": "linkedin",        "pages": 3,         "url": "https://www.linkedin.com/jobs/search?location=Finland&sortBy=DD"},
    {"id": "duunitori_broad",   "platform": "duunitori",       "scroll_count": 12, "url": "https://duunitori.fi/tyopaikat?jarjestys=uusimmat"},
    {"id": "indeed_fi_broad",   "platform": "indeed",          "pages": 4,         "url": "https://fi.indeed.com/jobs?l=Finland&sort=date"},
    {"id": "jobly_broad",       "platform": "jobly",           "scroll_count": 10, "url": "https://www.jobly.fi/tyopaikat"},
    {"id": "kuntarekry_broad",  "platform": "kuntarekry",      "scroll_count": 8,  "url": "https://www.kuntarekry.fi/fi/tyopaikat/?sort=-publish_time"},
    {"id": "tyomarkkinatori",   "platform": "tyomarkkinatori", "scroll_count": 10, "url": "https://tyomarkkinatori.fi/henkiloasiakkaat/avoimet-tyopaikat?sort=published,desc"},
    {"id": "meetfrank",         "platform": "meetfrank",       "scroll_count": 10, "url": "https://meetfrank.com/jobs/"},
    {"id": "work_in_finland",   "platform": "work_in_finland", "scroll_count": 8,  "url": "https://www.workinfinland.com/en/open-jobs/"},
]

_DEFAULT_SCROLL_COUNT = 8


def _page_url(base_url: str, platform: str, page_idx: int) -> str:
    if page_idx == 0:
        return base_url
    if platform == 'linkedin':
        return base_url + f'&start={page_idx * 25}'
    if platform == 'indeed':
        return base_url + f'&start={page_idx * 10}'
    return base_url


def _term_slug(term: str) -> str:
    """ASCII slug from a search term for use in target IDs."""
    normalized = unicodedata.normalize('NFKD', term)
    ascii_str = normalized.encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '_', ascii_str.lower()).strip('_')


def generate_targets():
    from urllib.parse import quote_plus
    targets = []

    # 1. Every keyword × every keyword-capable site (language-matched)
    for term in _KEYWORD_TERMS:
        slug = _term_slug(term["en"])  # slug always from English for stable IDs
        for tmpl in _KEYWORD_SITE_TEMPLATES:
            lang = tmpl.get("lang", "en")
            search_term = term.get(lang, term["en"])  # fall back to EN if no variant
            term_enc = quote_plus(search_term)
            base_url = tmpl['url_template'].replace('{term_enc}', term_enc)
            max_pages = tmpl.get('pages', 1)
            scroll_count = tmpl.get('scroll_count', _DEFAULT_SCROLL_COUNT)
            base_id = f"{tmpl['id_prefix']}_{slug}"
            for page_idx in range(max_pages):
                url = _page_url(base_url, tmpl['platform'], page_idx)
                tid = base_id if page_idx == 0 else f"{base_id}_p{page_idx + 1}"
                targets.append({'id': tid, 'platform': tmpl['platform'], 'term': search_term,
                                'url': url, 'scroll_count': scroll_count})

    # 2. Fixed sites (career pages, specialist boards — no keyword injection)
    for site in FIXED_SITES:
        max_pages = site.get('pages', 1)
        scroll_count = site.get('scroll_count', _DEFAULT_SCROLL_COUNT)
        for page_idx in range(max_pages):
            url = _page_url(site['url'], site['platform'], page_idx)
            tid = site['id'] if page_idx == 0 else f"{site['id']}_p{page_idx + 1}"
            targets.append({'id': tid, 'platform': site['platform'], 'term': 'All',
                            'url': url, 'scroll_count': scroll_count})

    return targets


# ─────────────────────────────────────────────────────────────────────────────
# PARSERS
# ─────────────────────────────────────────────────────────────────────────────

def parse_linkedin(soup):
    """Parse LinkedIn job search results page."""
    jobs = []
    for card in soup.find_all('div', class_='base-card'):
        title_elem = card.find('h3', class_='base-search-card__title')
        company_elem = card.find('h4', class_='base-search-card__subtitle')
        location_elem = card.find('span', class_='job-search-card__location')
        url_elem = card.find('a', class_='base-card__full-link')
        if title_elem and url_elem:
            title = title_elem.text.strip()
            # Check blocked keywords
            title_lower = title.lower()
            if any(kw in title_lower for kw in BLOCKED_TITLE_KEYWORDS):
                continue
            jobs.append({
                "title": title,
                "company": company_elem.text.strip() if company_elem else "Unknown",
                "location": location_elem.text.strip() if location_elem else "Unknown",
                "url": url_elem['href'].split('?')[0],
                "visited": "no",
                "matches_requirements": "pending",
                "reason": ""
            })
    return jobs


def parse_reddit(soup):
    """Parse old.reddit.com search results page."""
    jobs = []
    # Old reddit search results use class 'search-result'
    for post in soup.find_all('div', class_='search-result'):
        title_elem = post.find('a', class_='search-title')
        subreddit_elem = post.find('a', class_='search-subreddit-link')
        
        if title_elem:
            title = title_elem.text.strip()
            title_lower = title.lower()
            if any(kw in title_lower for kw in BLOCKED_TITLE_KEYWORDS):
                continue
                
            url = title_elem.get('href', '')
            if url and not url.startswith('http'):
                url = 'https://old.reddit.com' + url
                
            jobs.append({
                "title": title,
                "company": subreddit_elem.text.strip() if subreddit_elem else "Reddit",
                "location": "Global / Remote / See Post",
                "url": url.split('?')[0],
                "visited": "no",
                "matches_requirements": "pending",
                "reason": ""
            })
    return jobs


def parse_naukri(soup):
    """Parse Naukri.com job search results page."""
    jobs = []
    # Naukri uses article tags with class 'jobTuple' or similar card structures
    for card in soup.find_all(['article', 'div'], class_=re.compile(r'(jobTuple|srp-jobtuple|cust-job-tuple)', re.I)):
        title_elem = card.find(['a', 'h2'], class_=re.compile(r'(title|desig)', re.I))
        company_elem = card.find(['a', 'span'], class_=re.compile(r'(comp-name|companyInfo|subTitle)', re.I))
        location_elem = card.find(['span', 'li'], class_=re.compile(r'(loc|location|locWd498)', re.I))

        if title_elem:
            title = title_elem.text.strip()
            title_lower = title.lower()
            if any(kw in title_lower for kw in BLOCKED_TITLE_KEYWORDS):
                continue

            url = ''
            if title_elem.name == 'a' and title_elem.get('href'):
                url = title_elem['href']
            elif title_elem.find('a', href=True):
                url = title_elem.find('a', href=True)['href']

            if not url:
                link = card.find('a', href=True)
                if link:
                    url = link['href']

            if url and not url.startswith('http'):
                url = 'https://www.naukri.com' + url

            if url and title and len(title) > 3:
                jobs.append({
                    "title": title.replace('\n', ' ').strip(),
                    "company": company_elem.text.strip() if company_elem else "Unknown",
                    "location": location_elem.text.strip() if location_elem else "Unknown",
                    "url": url.split('?')[0],
                    "visited": "no",
                    "matches_requirements": "pending",
                    "reason": ""
                })
    
    # Fallback: try generic link extraction if no structured cards found
    if not jobs:
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/job-listings-' in href.lower() or '/job/' in href.lower():
                title = a.text.strip()
                if 5 < len(title) < 100:
                    title_lower = title.lower()
                    if any(kw in title_lower for kw in BLOCKED_TITLE_KEYWORDS):
                        continue
                    full_url = href if href.startswith('http') else 'https://www.naukri.com' + href
                    jobs.append({
                        "title": title.replace('\n', ' ').strip(),
                        "company": "Extract from page",
                        "location": "Extract from page",
                        "url": full_url.split('?')[0],
                        "visited": "no",
                        "matches_requirements": "pending",
                        "reason": ""
                    })
    return jobs


def parse_generic(soup, base_url):
    """Parse generic job board or career page by extracting links with job-related URL patterns."""
    jobs = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        href_lower = href.lower()
        path_part = href_lower.split('?')[0]

        # Must contain a job-related path keyword (English ATS patterns + Finnish job-board patterns,
        # the latter for Duunitori/Oikotie/Jobly/Kuntarekry/Tyomarkkinatori/Work in Finland)
        if not any(kw in href_lower for kw in ['/job', '/career', '/position', '/vacancy', '/opening',
                                                '/requisition', '/view', '/rc/clk', '/apply', '/posting',
                                                '/tyopaikka', '/tyopaikat/', '/avoimet-tyopaikat']):
            continue

        # Workday (and similar ATSes) append ?q=<search_term> to job detail URLs.
        # If the path contains /job/, this is a detail URL — strip the query string
        # so the ?q= skip below doesn't incorrectly discard it.
        if '/job/' in path_part and '?q=' in href_lower:
            href = href.split('?')[0]
            href_lower = href.lower()
            path_part = href_lower

        # Skip documents and static HTML info pages.
        # Real job detail URLs (Workday, Greenhouse, Lever, etc.) never end with a file extension.
        path_only = href_lower.split('?')[0].split('#')[0]
        if any(path_only.endswith(ext) for ext in ('.pdf', '.html', '.htm', '.doc', '.docx')):
            continue

        # Skip search/list filter queries, sorting options, base list pages, non-job pages
        if any(skip in href_lower for skip in [
            '?search=', '?q=', '?sort=', '?query=', '?haku=', 'jarjestys=',
            '/careers?', '/job-search?', '/search-jobs?',
            'destination=search', '/login', '/signup', '/register',
            '/pricing', '/about', '/contact', '/blog',
            '/job-bookmarks', '/saved-jobs', 'apply-now',
            'tyopaikat.oikotie.fi/tyopaikat?', 'rekrytointi', 'tyopaikkailmoitus',
            '/tyonantajalle', '/yhteystiedot', '/palvelut/',
        ]):
            continue

        clean_path = path_part.rstrip('/')
        if clean_path.endswith('/careers') or clean_path.endswith('/jobs') or clean_path.endswith('/openings') \
                or clean_path.endswith('/tyopaikat') or clean_path.endswith('/avoimet-tyopaikat'):
            continue

        title = a.text.strip()
        title_lower = title.lower()
        if any(kw in title_lower for kw in BLOCKED_TITLE_KEYWORDS):
            continue
        if 5 < len(title) < 100 and not any(skip in title.lower() for skip in [
            'read more', 'learn more', 'see all', 'show all', 'view all',
            'sign in', 'log in', 'apply now', 'click here'
        ]):
            raw_url = urljoin(base_url, href)
            # Normalize Indeed URLs to prevent duplicates
            if "indeed.com/rc/clk" in raw_url and "jk=" in raw_url:
                import urllib.parse
                parsed = urllib.parse.urlparse(raw_url)
                qs = urllib.parse.parse_qs(parsed.query)
                if 'jk' in qs:
                    raw_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?jk={qs['jk'][0]}"

            jobs.append({
                "title": title.replace('\n', ' ').strip(),
                "company": "Extract from page",
                "location": "Extract from page",
                "url": raw_url,
                "visited": "no",
                "matches_requirements": "pending",
                "reason": ""
            })
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# BACKUP SYSTEM
# ─────────────────────────────────────────────────────────────────────────────

def clean_old_backups(backup_dir):
    """Smart backup retention: keeps 1/hr for 24h, 1/day for 7d, 1/week for 4w."""
    if not os.path.exists(backup_dir):
        return

    now = datetime.now()
    files = glob.glob(os.path.join(backup_dir, "jobs_backup_*.json"))

    parsed_files = []
    for f in files:
        basename = os.path.basename(f)
        try:
            ts_str = basename.replace("jobs_backup_", "").replace(".json", "")
            file_time = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            parsed_files.append((f, file_time))
        except ValueError:
            continue

    # Sort files from newest to oldest
    parsed_files.sort(key=lambda x: x[1], reverse=True)

    keepers = set()
    seen_hours = set()
    seen_days = set()
    seen_weeks = set()

    for filepath, file_time in parsed_files:
        age = now - file_time

        # Keep latest backup unconditionally to never delete the one we just made
        if not keepers:
            keepers.add(filepath)
            continue

        if age <= timedelta(hours=24):
            hour_key = file_time.strftime("%Y%m%d_%H")
            if hour_key not in seen_hours:
                seen_hours.add(hour_key)
                keepers.add(filepath)
        elif age <= timedelta(days=8):
            day_key = file_time.strftime("%Y%m%d")
            if day_key not in seen_days:
                seen_days.add(day_key)
                keepers.add(filepath)
        elif age <= timedelta(days=36):
            week_key = f"{file_time.isocalendar()[0]}_{file_time.isocalendar()[1]}"
            if week_key not in seen_weeks:
                seen_weeks.add(week_key)
                keepers.add(filepath)

    deleted_count = 0
    for filepath, _ in parsed_files:
        if filepath not in keepers:
            try:
                os.remove(filepath)
                deleted_count += 1
            except Exception:
                pass

    if deleted_count > 0:
        print(f"INFO: Cleaned up {deleted_count} old backups (kept {len(keepers)}).")


def save_history_snapshot(jobs, added=0, deleted=0):
    """Append a count snapshot to jobs_history.json for trend tracking.

    added/deleted capture this run's gross churn (new jobs discovered vs. jobs
    removed by clean_blocked_jobs) so the dashboard can chart them as separate
    bars instead of only the net total delta.
    """
    snapshot = {
        "timestamp": datetime.now().isoformat(timespec='seconds'),
        "total": len(jobs),
        "yes": sum(1 for j in jobs if j.get('matches_requirements') == 'yes'),
        "no": sum(1 for j in jobs if j.get('matches_requirements') == 'no'),
        "maybe": sum(1 for j in jobs if j.get('matches_requirements') == 'maybe'),
        "pending": sum(1 for j in jobs if j.get('matches_requirements') == 'pending'),
        "applied": sum(1 for j in jobs if j.get('applied') == 'yes'),
        "added": added,
        "deleted": deleted,
    }
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except Exception:
            # Don't silently discard whatever history the file held - preserve
            # it for forensics/recovery instead of overwriting it with a
            # single-entry file.
            backup_path = HISTORY_FILE + f".corrupt-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                os.replace(HISTORY_FILE, backup_path)
                print(f"WARN: {HISTORY_FILE} was unreadable; backed up to {backup_path} and starting fresh.")
            except Exception:
                pass
            history = []
    history.append(snapshot)
    tmp_path = HISTORY_FILE + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2)
    os.replace(tmp_path, HISTORY_FILE)


def generate_history_from_backups():
    """One-time: build jobs_history.json from all existing backup files."""
    if os.path.exists(HISTORY_FILE):
        return  # Already generated
    backup_dir = os.path.join(BASE_DIR, "backups")
    files = sorted(glob.glob(os.path.join(backup_dir, "jobs_backup_*.json")))
    history = []
    for fpath in files:
        basename = os.path.basename(fpath)
        try:
            ts_str = basename.replace("jobs_backup_", "").replace(".json", "")
            ts = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            with open(fpath, 'r', encoding='utf-8') as f:
                jobs = json.load(f)
            history.append({
                "timestamp": ts.isoformat(timespec='seconds'),
                "total": len(jobs),
                "yes": sum(1 for j in jobs if j.get('matches_requirements') == 'yes'),
                "no": sum(1 for j in jobs if j.get('matches_requirements') == 'no'),
                "maybe": sum(1 for j in jobs if j.get('matches_requirements') == 'maybe'),
                "pending": sum(1 for j in jobs if j.get('matches_requirements') == 'pending'),
            })
        except Exception as e:
            print(f"WARN: Skipping {basename}: {e}")
    if history:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2)
        print(f"INFO: Generated jobs_history.json with {len(history)} snapshots from backups.")


# ─────────────────────────────────────────────────────────────────────────────
# CLEANUP — Auto-delete blocked/expired jobs
# ─────────────────────────────────────────────────────────────────────────────

def clean_blocked_jobs():
    """Finds and moves hard-rejected jobs from jobs.json to deleted.json based on job_requirements.md criteria."""
    if not os.path.exists(JOBS_FILE):
        return 0

    try:
        with open(JOBS_FILE, 'r', encoding='utf-8') as f:
            jobs = json.load(f)
    except Exception as e:
        print(f"Error reading {JOBS_FILE} during cleanup: {e}")
        return 0

    deleted_jobs = []
    if os.path.exists(DELETED_FILE):
        try:
            with open(DELETED_FILE, 'r', encoding='utf-8') as f:
                deleted_jobs = json.load(f)
        except Exception:
            pass

    cleaned_jobs = []
    moved_count = 0

    seen_deleted = {j.get('url') for j in deleted_jobs if j.get('url')}

    for job in jobs:
        # Never auto-delete jobs the user has already explicitly reviewed
        if job.get('user_review') == 'done' or job.get('applied') == 'yes':
            cleaned_jobs.append(job)
            continue

        title = job.get('title', '').lower()
        reason = job.get('reason', '').lower()

        is_blocked = False
        deletion_reason = ""

        # 1. Blocked title keywords (non-technical roles + C-suite)
        for kw in BLOCKED_TITLE_KEYWORDS:
            if kw in title:
                is_blocked = True
                deletion_reason = f"Title contains hard-rejection keyword '{kw}'"
                break

        # 2. US citizenship / no-visa-sponsorship requirement (detected in LLM reason)
        if not is_blocked:
            if any(phrase in reason for phrase in [
                "us citizenship", "security clearance required",
                "must be a u.s. citizen", "requires us citizenship",
                "no visa sponsorship", "no sponsorship",
                "us citizen only", "must be authorized to work in the us",
                "work authorization in the us",
            ]):
                is_blocked = True
                deletion_reason = "Requires US citizenship / no visa sponsorship"

        # 3. Expired deadline (> 2 days passed)
        if not is_blocked:
            deadline_str = job.get('deadline', '')
            if deadline_str and re.match(r'^\d{4}-\d{2}-\d{2}$', str(deadline_str)):
                try:
                    deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d")
                    if datetime.now() > deadline_date + timedelta(days=2):
                        is_blocked = True
                        deletion_reason = f"Deadline ({deadline_str}) passed by more than 2 days and unreviewed"
                except ValueError:
                    pass

        if is_blocked:
            job['deletion_reason'] = deletion_reason
            if job.get('url') not in seen_deleted:
                deleted_jobs.append(job)
                seen_deleted.add(job.get('url'))
            moved_count += 1
        else:
            cleaned_jobs.append(job)

    if moved_count > 0:
        print(f"INFO: Moved {moved_count} hard-rejected jobs to deleted.json.")
        try:
            with open(JOBS_FILE, 'w', encoding='utf-8') as f:
                json.dump(cleaned_jobs, f, indent=2)
            with open(DELETED_FILE, 'w', encoding='utf-8') as f:
                json.dump(deleted_jobs, f, indent=2)
        except Exception as e:
            print(f"Error saving files during cleanup: {e}")

    return moved_count


# ─────────────────────────────────────────────────────────────────────────────
# SCRAPING ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def scrape_all_jobs(max_jobs=200):
    """Main scraping function: navigates to each source, extracts job listings, deduplicates, saves."""
    seen_urls = set()
    if os.path.exists(SEEN_URLS_FILE):
        try:
            with open(SEEN_URLS_FILE, 'r', encoding='utf-8') as f:
                seen_urls = set(json.load(f))
        except Exception:
            pass

    # Seed seen_urls from existing jobs so a stale seen_urls.json can't cause duplicates
    existing_jobs = []
    if os.path.exists(JOBS_FILE):
        try:
            with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                existing_jobs = json.load(f)
            seen_urls.update(j['url'] for j in existing_jobs)
        except Exception:
            pass

    checkpoint_idx = 0
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                checkpoint_idx = data.get("target_index", 0)
        except Exception:
            pass

    targets = generate_targets()
    if checkpoint_idx >= len(targets):
        print("Reached end of all targets. Resetting checkpoint to 0.")
        checkpoint_idx = 0

    all_extracted_jobs = []
    limit = max_jobs if max_jobs > 0 else float('inf')

    with sync_playwright() as p:
        print("Launching Playwright browser...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        page.set_default_navigation_timeout(60000)
        page.set_default_timeout(60000)

        current_idx = checkpoint_idx
        while current_idx < len(targets) and len(all_extracted_jobs) < limit:
            target = targets[current_idx]
            print(f"\nNavigating to {target['url']} (Site: {target['id']}) ...")
            try:
                page.goto(target['url'], timeout=60000)
                scroll_count = target.get('scroll_count', _DEFAULT_SCROLL_COUNT)
                for _ in range(scroll_count):
                    page.mouse.wheel(0, 2000)
                    time.sleep(1.5)

                soup = BeautifulSoup(page.content(), 'html.parser')

                if target['platform'] == 'linkedin':
                    jobs = parse_linkedin(soup)
                elif target['platform'] == 'reddit':
                    jobs = parse_reddit(soup)
                elif target['platform'] == 'naukri':
                    jobs = parse_naukri(soup)
                else:
                    jobs = parse_generic(soup, target['url'])

                print(f"Found {len(jobs)} potential job links on {target['platform']}.")

                added = 0
                for job in jobs:
                    if len(all_extracted_jobs) >= limit:
                        break
                    if job['url'] not in seen_urls:
                        job['id'] = hashlib.md5(job['url'].encode('utf-8')).hexdigest()[:8]
                        job['source'] = target['id']
                        all_extracted_jobs.append(job)
                        seen_urls.add(job['url'])
                        added += 1

                print(f"Added {added} new unseen jobs from this source.")
            except Exception as e:
                print(f"Failed to scrape {target['id']}: {e}")
                # Reset page to a clean state so the next goto() isn't interrupted
                # by an unresolved navigation left behind by this failure.
                try:
                    page.goto("about:blank", timeout=5000)
                except Exception:
                    pass

            current_idx += 1

        browser.close()

    print(f"\nTotal new jobs fetched in this run: {len(all_extracted_jobs)}")
    print(f"Stopped at target index: {current_idx} out of {len(targets)}")

    # Merge: filter out any URLs already in existing_jobs (guards against seen_urls desync)
    existing_urls = {j['url'] for j in existing_jobs}
    deduped_new = [j for j in all_extracted_jobs if j['url'] not in existing_urls]
    if len(deduped_new) < len(all_extracted_jobs):
        print(f"INFO: Dropped {len(all_extracted_jobs) - len(deduped_new)} duplicate URLs at merge time.")
    combined_jobs = existing_jobs + deduped_new

    # Save combined jobs to jobs.json
    with open(JOBS_FILE, 'w', encoding='utf-8') as f:
        json.dump(combined_jobs, f, indent=2)

    # Create a timestamped backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BASE_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_file = os.path.join(backup_dir, f"jobs_backup_{timestamp}.json")
    with open(backup_file, 'w', encoding='utf-8') as f:
        json.dump(combined_jobs, f, indent=2)

    print(f"Total jobs currently in jobs.json: {len(combined_jobs)}")
    print(f"Backup saved to: {backup_file}\n")

    # Append to history for trend chart
    save_history_snapshot(combined_jobs, added=len(deduped_new))

    # Run smart cleanup
    clean_old_backups(backup_dir)

    # Save updated seen urls history
    with open(SEEN_URLS_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(seen_urls), f)

    # Save checkpoint (merge, don't clobber other keys like requirements_hash)
    _checkpoint_data = {}
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                _checkpoint_data = json.load(f)
        except Exception:
            pass
    _checkpoint_data["target_index"] = current_idx
    with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as f:
        json.dump(_checkpoint_data, f, indent=2)

    return all_extracted_jobs


# ─────────────────────────────────────────────────────────────────────────────
# REQUIREMENTS CHANGE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def get_file_hash(filepath):
    """Compute MD5 hash of a file."""
    if not os.path.exists(filepath):
        return ""
    with open(filepath, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

def _load_last_llm_model():
    """Read the last successfully-detected LLM model identifier from checkpoint.json."""
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                return json.load(f).get("last_llm_model")
        except Exception:
            pass
    return None

def _save_last_llm_model(model_name):
    """Persist the last successfully-detected LLM model to checkpoint.json (merge,
    don't clobber other keys), so there's a real fallback if LM Studio is later
    unreachable - better than an env var that can point at a model that's been
    uninstalled or was only ever set once and forgotten."""
    _checkpoint_data = {}
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                _checkpoint_data = json.load(f)
        except Exception:
            pass
    if _checkpoint_data.get("last_llm_model") == model_name:
        return  # Avoid a disk write on every call when nothing has changed
    _checkpoint_data["last_llm_model"] = model_name
    with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as f:
        json.dump(_checkpoint_data, f, indent=2)

def get_active_llm_model(endpoint):
    """Ask LM Studio (via `lms ps`) which chat model is actually loaded right now.
    NOTE: the OpenAI-compatible /v1/models HTTP endpoint is NOT usable for this - it
    lists every downloaded model eligible for LM Studio's just-in-time loading, not
    just what's currently resident in memory, so picking the first entry from it is
    not reliable active-model detection. `lms ps` only ever lists genuinely loaded
    models, so it's the only trustworthy source here.
    A manual model switch in the LM Studio GUI (or via /set_local_llm) takes effect
    immediately this way - no env var to keep in sync, which is what let LM Studio
    silently swap in a different model mid-request via its just-in-time loading.
    If lms.exe is unreachable or nothing is loaded, falls back to whichever model
    was last successfully detected (persisted in checkpoint.json), and only as a
    last resort to the LOCAL_LLM_MODEL env var (relevant for a first-ever run with
    no prior state saved yet)."""
    try:
        lms_exe = os.path.join(os.environ.get("USERPROFILE", ""), ".lmstudio", "bin", "lms.exe")
        result = subprocess.run([lms_exe, "ps", "--json"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            models = json.loads(result.stdout)
            chat_models = [m["identifier"] for m in models if m.get("type") == "llm"]
            if chat_models:
                _save_last_llm_model(chat_models[0])
                return chat_models[0]
    except Exception:
        pass
    return _load_last_llm_model() or os.environ.get("LOCAL_LLM_MODEL")


def check_requirements_update():
    """Check if job_requirements.md has changed, and flag jobs for re-evaluation if it has."""
    req_hash = get_file_hash(REQ_FILE)
    if not req_hash:
        return

    checkpoint_data = {}
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                checkpoint_data = json.load(f)
        except Exception:
            pass

    saved_hash = checkpoint_data.get("requirements_hash", "")
    if saved_hash and req_hash != saved_hash:
        print("INFO: job_requirements.md has changed! Resetting evaluation status for existing jobs...")
        if os.path.exists(JOBS_FILE):
            with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                jobs = json.load(f)
            for job in jobs:
                if job.get('user_review') != 'done':
                    job['needs_re_review'] = True
            with open(JOBS_FILE, 'w', encoding='utf-8') as f:
                json.dump(jobs, f, indent=2)

    checkpoint_data["requirements_hash"] = req_hash
    with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as f:
        json.dump(checkpoint_data, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# TEXT PROCESSING UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def extract_json_from_text(text):
    """Finds and parses the first JSON object in a text block, handling markdown code fences."""
    import re
    text = text.strip()
    start_idx = text.find('{')
    end_idx = text.rfind('}')
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidate = text[start_idx:end_idx + 1]
        
        # Remove trailing commas which are common in LLM outputs
        candidate = re.sub(r',\s*}', '}', candidate)
        candidate = re.sub(r',\s*]', ']', candidate)
        
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
            
    # Fallback to loading the whole string, also stripping trailing commas
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)
    return json.loads(text)


def clean_page_text(text):
    """Strip cookie banners, navigation, and footer boilerplate from scraped job page text.

    This preprocessing improves LLM accuracy by removing noise that causes hallucinations
    (e.g., the LLM extracting the job board name as company name).
    """
    lines = text.split('\n')
    cleaned_lines = []

    # Common boilerplate patterns to skip (case-insensitive matching)
    skip_patterns = [
        # Cookie consent / GDPR banners
        'cookie settings', 'accept all cookies', 'reject all', 'cookie policy',
        'we use cookies', 'manage cookies', 'cookie preferences',
        # Navigation / chrome
        'skip to main content', 'sign in', 'log in', 'create account',
        'sign up', 'forgot password',
        # Search UI
        'find jobs', 'search jobs', 'browse jobs', 'job search',
        # Footer
        '© 20', 'all rights reserved', 'privacy policy', 'terms of use',
        'terms and conditions', 'privacy center', 'accessibility',
        # Action prompts
        'apply on company site', 'easy apply', 'save job',
        'report this job', 'flag as', 'share this',
        # Anti-bot
        'checking your browser', 'ddos-guard', 'please stand by',
        'please allow up to', 'cloudflare',
        # Social sharing
        'share on linkedin', 'share on twitter', 'share on facebook',
    ]

    # Single-word nav items to skip (exact match on stripped line)
    nav_words = {'home', 'about', 'help', 'contact', 'blog', 'faq', 'login', 'signup'}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()

        # Skip lines that match boilerplate patterns
        if any(pat in lower for pat in skip_patterns):
            continue

        # Skip single nav words
        if lower in nav_words:
            continue

        # Skip very short lines that are just UI labels (1-2 chars or just &nbsp;)
        if len(stripped) <= 2 or stripped == '&nbsp;':
            continue

        cleaned_lines.append(stripped)

    return '\n'.join(cleaned_lines)


def standardize_date(date_str):
    """Standardizes various date formats into YYYY-MM-DD."""
    if not date_str: return 'N/A'
    date_str = str(date_str).strip().lower()
    if date_str in ['n/a', 'unknown', 'not specified', 'none', 'null']: return 'N/A'
    if 'open' in date_str: return 'Open until filled'

    # Handle ranges by taking the end date
    if '-' in date_str and not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        parts = date_str.split('-')
        date_str = parts[-1].strip()

    # Standard YYYY-MM-DD
    if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return date_str

    # Indian / European DD/MM/YYYY or DD-MM-YYYY
    slash_match = re.match(r'^(\d{1,2})[/](\d{1,2})[/](\d{4})$', date_str)
    if slash_match:
        day, month, year = int(slash_match.group(1)), int(slash_match.group(2)), int(slash_match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # Finnish / European DD.MM.YYYY
    fi_match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$', date_str)
    if fi_match:
        return f"{int(fi_match.group(3)):04d}-{int(fi_match.group(2)):02d}-{int(fi_match.group(1)):02d}"

    # Incomplete DD.MM. (assume current year)
    fi_short = re.match(r'^(\d{1,2})\.(\d{1,2})\.?$', date_str)
    if fi_short:
        year = datetime.now().year
        return f"{year:04d}-{int(fi_short.group(2)):02d}-{int(fi_short.group(1)):02d}"

    # ISO datetime
    try:
        if 't' in date_str:
            d = datetime.fromisoformat(date_str.split('t')[0])
            return d.strftime("%Y-%m-%d")
    except ValueError:
        pass

    today = datetime.now()

    # "today", "yesterday"
    if 'today' in date_str:
        return today.strftime("%Y-%m-%d")
    if 'yesterday' in date_str:
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")

    # Relative formats: "X days ago", "X weeks ago", "X months ago"
    rel_match = re.search(r'(\d+)\s+(day|week|month|hour|min)', date_str)
    if rel_match:
        num = int(rel_match.group(1))
        unit = rel_match.group(2)
        if 'day' in unit:
            return (today - timedelta(days=num)).strftime("%Y-%m-%d")
        if 'week' in unit:
            return (today - timedelta(weeks=num)).strftime("%Y-%m-%d")
        if 'month' in unit:
            return (today - timedelta(days=num * 30)).strftime("%Y-%m-%d")
        if 'hour' in unit or 'min' in unit:
            return today.strftime("%Y-%m-%d")

    # Also "X d ago", "X w ago"
    short_rel = re.search(r'(\d+)\s*(d|w|m)\s+ago', date_str)
    if short_rel:
        num = int(short_rel.group(1))
        unit = short_rel.group(2)
        if unit == 'd': return (today - timedelta(days=num)).strftime("%Y-%m-%d")
        if unit == 'w': return (today - timedelta(weeks=num)).strftime("%Y-%m-%d")
        if unit == 'm': return (today - timedelta(days=num * 30)).strftime("%Y-%m-%d")

    # Month words: "June 15, 2026" or "15 June 2026"
    try:
        months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
        months_short = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        parts = date_str.replace(',', '').split()
        if len(parts) >= 3:
            year_part = [p for p in parts if p.isdigit() and len(p) == 4]
            day_part = [p for p in parts if p.isdigit() and len(p) <= 2]
            month_part = [p for p in parts if p in months or p in months_short]
            if year_part and day_part and month_part:
                m_str = month_part[0]
                m_idx = months.index(m_str) + 1 if m_str in months else months_short.index(m_str) + 1
                y = int(year_part[0])
                d = int(day_part[0])
                return f"{y:04d}-{m_idx:02d}-{d:02d}"
    except Exception:
        pass

    return date_str


# ─────────────────────────────────────────────────────────────────────────────
# LOCATION & COMPANY EXTRACTION (Regex fallbacks)
# ─────────────────────────────────────────────────────────────────────────────

def extract_location_from_text(text):
    """Extract location from job page text using regex patterns.

    Looks for known city names from semiconductor hubs worldwide, Indian cities,
    and structured location patterns.
    """
    lines = text.split('\n')

    # Indian cities and their metro areas
    india_cities = {
        'Bengaluru': 'Bengaluru, India', 'Bangalore': 'Bengaluru, India',
        'Hyderabad': 'Hyderabad, India', 'Chennai': 'Chennai, India',
        'Pune': 'Pune, India', 'Mumbai': 'Mumbai, India',
        'Delhi': 'Delhi NCR, India', 'New Delhi': 'Delhi NCR, India',
        'Noida': 'Delhi NCR, India', 'Gurugram': 'Delhi NCR, India',
        'Gurgaon': 'Delhi NCR, India', 'Greater Noida': 'Delhi NCR, India',
        'Ahmedabad': 'Ahmedabad, India', 'Kolkata': 'Kolkata, India',
        'Thiruvananthapuram': 'Thiruvananthapuram, India',
        'Trivandrum': 'Thiruvananthapuram, India',
        'Kochi': 'Kochi, India', 'Cochin': 'Kochi, India',
        'Coimbatore': 'Coimbatore, India', 'Mysuru': 'Mysuru, India',
        'Mysore': 'Mysuru, India', 'Chandigarh': 'Chandigarh, India',
        'Lucknow': 'Lucknow, India', 'Jaipur': 'Jaipur, India',
        'Bhubaneswar': 'Bhubaneswar, India', 'Visakhapatnam': 'Visakhapatnam, India',
        'Nagpur': 'Nagpur, India', 'Indore': 'Indore, India',
        'Mohali': 'Mohali, India',
    }

    # Global semiconductor hubs
    global_cities = {
        'San Jose': 'San Jose, CA, USA', 'Santa Clara': 'Santa Clara, CA, USA',
        'San Francisco': 'San Francisco, CA, USA', 'San Diego': 'San Diego, CA, USA',
        'Austin': 'Austin, TX, USA', 'Dallas': 'Dallas, TX, USA',
        'Portland': 'Portland, OR, USA', 'Hillsboro': 'Hillsboro, OR, USA',
        'Boise': 'Boise, ID, USA', 'Chandler': 'Chandler, AZ, USA',
        'Folsom': 'Folsom, CA, USA', 'Milpitas': 'Milpitas, CA, USA',
        'Sunnyvale': 'Sunnyvale, CA, USA', 'Cupertino': 'Cupertino, CA, USA',
        'Mountain View': 'Mountain View, CA, USA', 'Irvine': 'Irvine, CA, USA',
        'Munich': 'Munich, Germany', 'Eindhoven': 'Eindhoven, Netherlands',
        'Leuven': 'Leuven, Belgium', 'Hsinchu': 'Hsinchu, Taiwan',
        'Tainan': 'Tainan, Taiwan', 'Seoul': 'Seoul, South Korea',
        'Icheon': 'Icheon, South Korea', 'Singapore': 'Singapore',
        'Penang': 'Penang, Malaysia', 'Dresden': 'Dresden, Germany',
        'Sophia Antipolis': 'Sophia Antipolis, France',
        'Grenoble': 'Grenoble, France', 'Cambridge': 'Cambridge, UK',
        'Bristol': 'Bristol, UK', 'Edinburgh': 'Edinburgh, UK',
        'London': 'London, UK', 'Espoo': 'Espoo, Finland',
        'Tampere': 'Tampere, Finland', 'Oulu': 'Oulu, Finland',
        'Helsinki': 'Helsinki, Finland', 'Turku': 'Turku, Finland',
        'Dublin': 'Dublin, Ireland', 'Cork': 'Cork, Ireland',
        'Tokyo': 'Tokyo, Japan', 'Yokohama': 'Yokohama, Japan',
        'Shenzhen': 'Shenzhen, China', 'Shanghai': 'Shanghai, China',
        'Beijing': 'Beijing, China', 'Tel Aviv': 'Tel Aviv, Israel',
        'Haifa': 'Haifa, Israel',
    }

    all_cities = {**india_cities, **global_cities}

    # Pattern 1: Line that contains "Location:" or "Location :"
    for line in lines:
        stripped = line.strip()
        if re.match(r'^(location|work location|job location)\s*[:\-]', stripped.lower()):
            loc_text = re.sub(r'^(location|work location|job location)\s*[:\-]\s*', '', stripped, flags=re.I).strip()
            if loc_text and len(loc_text) < 80:
                return loc_text

    # Pattern 2: Known city names mentioned in text
    text_words = set(re.findall(r'\b[A-Za-z]+(?:\s[A-Za-z]+)?\b', text))
    for city, formatted in all_cities.items():
        if city in text or city.lower() in text.lower():
            return formatted

    # Pattern 3: India/Remote inference
    text_lower = text.lower()
    if 'remote' in text_lower and ('worldwide' in text_lower or 'global' in text_lower):
        return "Remote (Worldwide)"
    if 'india' in text_lower and 'remote' in text_lower:
        return "Remote, India"

    return None


def extract_company_from_text(text, job_title):
    """Extract company name from job page text using structural patterns."""
    lines = text.split('\n')

    # Pattern 1: Line immediately after the job title
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and job_title and stripped.lower() == job_title.lower():
            # The next non-empty line is typically the company name
            for j in range(i + 1, min(i + 3, len(lines))):
                next_line = lines[j].strip()
                if next_line and len(next_line) > 2 and len(next_line) < 80:
                    # Skip common non-company lines
                    if next_line.lower() in ['description', 'overview', 'about', 'requirements',
                                              'apply now', 'save', 'share', 'location']:
                        continue
                    return next_line

    # Pattern 2: Look for company indicators
    company_patterns = [
        re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Technologies|Semiconductor[s]?|Electronics|Solutions|Systems|Inc|Corp|Ltd|Pvt|Limited|LLP))\b'),
        re.compile(r'\b((?:Tata|Wipro|Infosys|HCL|TCS|Cognizant)\s+\w+)\b'),
    ]
    for pattern in company_patterns:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()

    return None


# ─────────────────────────────────────────────────────────────────────────────
# AI-POWERED JOB EVALUATION
# ─────────────────────────────────────────────────────────────────────────────

def analyze_scrape_run_log(lines: list[str]):
    """Send lines captured during a full scrape cycle to the LLM and print a health report."""
    llm_endpoint = os.environ.get("LOCAL_LLM_ENDPOINT")
    llm_model = get_active_llm_model(llm_endpoint)
    if not llm_endpoint or not llm_model:
        return

    relevant = [l for l in lines if any(kw in l for kw in [
        'Navigating to', 'Found ', 'potential job links', 'Failed to scrape', 'Added ',
    ])]
    if not relevant:
        return

    log_text = '\n'.join(relevant)
    prompt = (
        "You are analysing the console log of a job-scraper run.\n"
        "Each search source prints three lines:\n"
        "  Navigating to <url> (Term: All, Site: <id>) ...\n"
        "  Found N potential job links on <platform>.\n"
        "  Added M new unseen jobs from this source.\n\n"
        "Task: identify every source that returned 0 potential job links. "
        "For each, note the site ID and URL. "
        "If ALL sources returned results, just say 'All sources returned results.' "
        "Be concise — bullet points only, no preamble.\n\n"
        f"Log:\n{log_text}"
    )

    try:
        resp_json, llm_used = _call_llm_with_fallback(
            [{"role": "user", "content": prompt}],
            llm_endpoint, llm_model, temperature=0.2, max_tokens=600, timeout_local=60,
        )
        report = resp_json['choices'][0]['message']['content'].strip()
        print(f"\n{'='*50}")
        print(f"SCRAPE HEALTH REPORT (LLM: {llm_used})")
        print('='*50)
        print(report)
        print('='*50)
    except Exception as e:
        print(f"[log-analysis] LLM health report failed: {e}")


def _wait_for_external_llm_idle(endpoint, poll_interval=2):
    """Best-effort backoff for consumers of the shared LM Studio server outside
    our own lock scheme (e.g. OpenClaw, ~/.openclaw - a separate agent framework
    also configured to use this same server). We can't have it claim a lock file
    the way manju_jobs/vineeth_jobs do for each other, so instead we poll LM
    Studio's own reported state via `lms ps --json` (status/queued per model) and
    wait while anything is actively generating or queued, before starting our own
    request. This is a courtesy, not a hard guarantee - if `lms.exe` is
    unreachable or the check fails for any reason, we proceed immediately rather
    than block the pipeline on a broken diagnostic."""
    lms_exe = os.path.join(os.environ.get("USERPROFILE", ""), ".lmstudio", "bin", "lms.exe")
    while not stop_event.is_set():
        try:
            result = subprocess.run([lms_exe, "ps", "--json"], capture_output=True, text=True, timeout=10)
            if result.returncode != 0 or not result.stdout.strip():
                return
            models = json.loads(result.stdout)
            busy = any(
                m.get("status") not in (None, "idle") or (m.get("queued") or 0) > 0
                for m in models if m.get("type") == "llm"
            )
            if not busy:
                return
        except Exception:
            return
        time.sleep(poll_interval)

def _post_llm_with_retry(url, headers, payload, timeout=120, retries=2, backoff_seconds=10):
    """POST to the local LLM, retrying on transient network errors (read timeout,
    connection reset). manju_jobs, vineeth_jobs, and priya_jobs scrapers share one
    single-threaded (parallel=1) LM Studio server, so a request can queue behind
    another scraper's in-flight generation and blow past a one-shot timeout or
    get its connection dropped - a short retry lets that queue drain instead of
    permanently marking the job 'error' for this run.

    Priority order for this shared LM Studio server: any external consumer
    (e.g. OpenClaw) > manju_jobs > vineeth_jobs > priya_jobs (lowest). We back off
    for external consumers using LM Studio's own reported busy/queued state (see
    _wait_for_external_llm_idle), since we don't control their code and can't have
    them claim a lock file. We then also back off for manju_jobs and vineeth_jobs
    specifically (both have priority over us): before competing for the pipeline
    lock, this backs off whenever either is waiting for or holding its own
    priority lock. priya_jobs claims no priority lock of its own - nothing is
    lower priority than it.

    Only the HTTP call itself is serialized against the sibling dashboards'
    scrapers (via PIPELINE_LOCK_FILE) - the LLM is only in use for the brief span
    of this call, so scraping/page-extraction elsewhere runs unlocked and all
    dashboards can browse concurrently. PIPELINE_LOCK_FILE, MANJU_PRIORITY_LOCK_FILE,
    and VINEETH_PRIORITY_LOCK_FILE are all real OS-level file locks, so a crashed
    process releases them automatically - no stale-flag cleanup needed."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            _wait_for_external_llm_idle(url)

            for priority_lock_file in (MANJU_PRIORITY_LOCK_FILE, VINEETH_PRIORITY_LOCK_FILE):
                priority_lock = FileLock(priority_lock_file)
                while not stop_event.is_set():
                    try:
                        priority_lock.acquire(timeout=0.1)
                        priority_lock.release()
                        break  # not currently claimed - proceed to check the next one
                    except Timeout:
                        time.sleep(1)  # someone above us has priority right now - wait and re-check
                if stop_event.is_set():
                    raise requests.exceptions.RequestException("Stopping - stop_event set while waiting for priority lock")

            with FileLock(PIPELINE_LOCK_FILE):
                response = requests.post(url, headers=headers, json=payload, timeout=timeout)
                response.raise_for_status()
            return response
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = e
            if attempt < retries:
                print(f"  [LLM] {e.__class__.__name__} on attempt {attempt + 1}/{retries + 1}, retrying in {backoff_seconds}s...")
                time.sleep(backoff_seconds)
    if last_err is not None:
        raise last_err
    raise requests.exceptions.RequestException("LLM request failed with no captured exception")

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
# Ordered by preference (quality first). Each Groq model has its OWN separate free-tier
# rate-limit quota, so rotating through them on failure meaningfully increases total
# throughput before giving up and falling back to local - a 429 on one model doesn't mean
# the others are limited too. Overridable via GROQ_MODELS (comma-separated). qwen/qwen3.6-27b
# leaks its <think>...</think> reasoning directly into `content` instead of a separate field
# like the gpt-oss models do - validated 2026-08-21 that extract_json_from_text's
# find-first-'{'/last-'}' approach tolerates this fine, but a long enough thinking chain
# could still eat the whole max_tokens budget before the actual JSON appears, truncating the
# response; kept last in the list for that reason. openai/gpt-oss-safeguard-20b (moderation-
# tuned) validated 2026-08-21 against a real job-matching prompt - clean content/reasoning
# separation like the other gpt-oss models, correct judgment, no unwanted refusals on normal
# job-posting text. Deliberately still excludes: groq/compound(-mini) (agentic meta-models
# with tool-use, untested for this single-shot extraction use case), allam-2-7b (Arabic-
# specialized, poor fit for English/Finnish text), and meta-llama/llama-prompt-guard-2-*
# (confirmed 2026-08-21 via direct API test to return a raw injection-likelihood probability
# score instead of text - not a chat model at all, would fail every single call).
GROQ_MODELS = [m.strip() for m in os.environ.get("GROQ_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b,openai/gpt-oss-safeguard-20b,qwen/qwen3.6-27b").split(",") if m.strip()]

_cloud_model_cooldown_until = {}  # "label/model" -> epoch seconds; see _try_cloud_provider

def _try_cloud_provider(messages, endpoint, api_key, models, temperature, max_tokens, timeout, label):
    """Try each model in `models` against `endpoint` in order, stopping at the first
    success (single attempt per model, no retry loop - see _call_llm_with_fallback for
    why). Returns (response_json, provider_label) on success, or None if every model
    failed, so the caller can move on to the next provider/the local fallback.

    Models that just returned 429 are skipped (not re-attempted) until the cooldown
    Groq's own `retry-after` header names has elapsed - Groq's per-model daily token quota
    (e.g. 200000 TPD on the free tier) is a rolling window, not a hard reset-at-midnight
    cap, so brief retries genuinely can succeed again soon; but under sustained heavy load
    (two scrapers sharing one Groq account) blindly re-trying an already-exhausted model on
    every single job wastes a request and floods the log with a failure we already know is
    coming. Tracked in-memory per process (not shared between manju_jobs/vineeth_jobs, and
    reset on restart) - a reasonable approximation given they can't easily share state."""
    for model in models:
        key = f"{label}/{model}"
        cooldown_until = _cloud_model_cooldown_until.get(key, 0)
        if time.time() < cooldown_until:
            continue
        try:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                try:
                    cooldown_seconds = float(retry_after)
                except (TypeError, ValueError):
                    cooldown_seconds = 60
                _cloud_model_cooldown_until[key] = time.time() + cooldown_seconds
                print(f"  [LLM] {label} model '{model}' rate-limited, cooling down {cooldown_seconds:.0f}s, trying next...")
                continue
            response.raise_for_status()
            return response.json(), f"{label}/{model}"
        except Exception as e:
            print(f"  [LLM] {label} model '{model}' failed ({e}), trying next...")
    return None

def _call_llm_with_fallback(messages, llm_endpoint, llm_model, temperature=0.1, max_tokens=500, timeout_local=120, timeout_groq=60):
    """Try Groq first (free-tier cloud inference, no local contention), rotating through
    GROQ_MODELS in order until one succeeds, falling back to the local LM Studio server
    only once every Groq model has failed - missing GROQ_API_KEY, network error, rate
    limit, bad response, anything. Each attempt is a single shot with no retry loop of its
    own: a failure there just means "not available right now", and moving on (to the next
    model, then to local - which already retries/waits its turn, see _post_llm_with_retry)
    is a better use of that time than retrying against what might be a persistent rate
    limit.

    Returns (response_json, provider_label) so callers can log/record which LLM actually
    produced the result - provider_label is e.g. 'groq/openai/gpt-oss-120b' or
    'local/<model>'."""
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if groq_api_key:
        result = _try_cloud_provider(messages, GROQ_ENDPOINT, groq_api_key, GROQ_MODELS, temperature, max_tokens, timeout_groq, "groq")
        if result:
            return result

    headers = {"Content-Type": "application/json"}
    llm_api_key = os.environ.get("LOCAL_LLM_API_KEY")
    if llm_api_key:
        headers["Authorization"] = f"Bearer {llm_api_key}"
    local_payload = {
        "model": llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    response = _post_llm_with_retry(llm_endpoint, headers, local_payload, timeout=timeout_local)
    return response.json(), f"local/{llm_model}"

def review_pending_jobs(specific_urls=None):
    """Visit URLs of pending jobs, extract description, and evaluate using a local LLM."""
    if not os.path.exists(JOBS_FILE):
        return

    with open(JOBS_FILE, 'r', encoding='utf-8') as f:
        jobs = json.load(f)

    if specific_urls is not None:
        pending_jobs = [j for j in jobs if (j.get('matches_requirements') in ['pending', 'error'] or j.get('needs_re_review') == True) and j['url'] in specific_urls]
    else:
        pending_jobs = [j for j in jobs if j.get('matches_requirements') in ['pending', 'error'] or j.get('needs_re_review') == True]

    if not pending_jobs:
        return

    llm_endpoint = os.environ.get("LOCAL_LLM_ENDPOINT")
    llm_model = get_active_llm_model(llm_endpoint)

    if not llm_endpoint or not llm_model:
        print("ERROR: LOCAL_LLM_ENDPOINT and LOCAL_LLM_MODEL environment variables must be set to use a local LLM. Skipping review.")
        print("INFO: Examples to set variables:")
        print("      Bash (Linux/WSL):    export LOCAL_LLM_ENDPOINT='http://localhost:11434/v1/chat/completions'")
        print("                           export LOCAL_LLM_MODEL='llama3'")
        print("      PowerShell (Windows):$env:LOCAL_LLM_ENDPOINT='http://localhost:11434/v1/chat/completions'")
        print("                           $env:LOCAL_LLM_MODEL='llama3'")
        print("      CMD (Windows):       set LOCAL_LLM_ENDPOINT=http://localhost:11434/v1/chat/completions")
        print("                           set LOCAL_LLM_MODEL=llama3")
        return

    print(f"\nEvaluating {len(pending_jobs)} pending jobs using local LLM at {llm_endpoint} with model {llm_model}...")

    requirements_text = ""
    if os.path.exists(REQ_FILE):
        with open(REQ_FILE, 'r') as f:
            requirements_text = f.read()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        page.set_default_navigation_timeout(60000)
        page.set_default_timeout(60000)

        for job in pending_jobs:
            if stop_event.is_set():
                break
            print(f"Reviewing: {job['title']} at {job['url']}")
            try:
                page.goto(job['url'], timeout=60000)

                # Smart wait: detect DDoS guard / anti-bot pages and retry
                text = ""
                ddos_keywords = ['ddos-guard', 'checking your browser', 'please stand by', 'please allow up to']
                max_retries = 4
                for attempt in range(max_retries):
                    wait_time = 1.5 if attempt == 0 else 3.0
                    time.sleep(wait_time)
                    try:
                        text = page.locator('body').inner_text()
                    except Exception:
                        text = ""

                    text_lower = text.lower().strip()
                    # Check if the page is still showing a DDoS guard / anti-bot interstitial
                    if any(kw in text_lower for kw in ddos_keywords) and len(text) < 1500:
                        print(f"  [Attempt {attempt + 1}/{max_retries}] Anti-bot page detected, waiting longer...")
                        continue
                    else:
                        break

                posted_date = "N/A"
                deadline = "N/A"

                # Sanity check: if text is empty or still a DDoS guard page, treat as error
                text_lower_check = text.lower().strip()
                is_ddos_page = any(kw in text_lower_check for kw in ddos_keywords) and len(text) < 1500
                llm_used = None
                if not text.strip() or is_ddos_page:
                    match, reason = "error", "Could not extract text from page (anti-bot protection or empty page)."
                else:
                    # Clean page text to remove cookie banners, nav, footers
                    cleaned_text = clean_page_text(text)

                    today_str = datetime.now().strftime("%Y-%m-%d")
                    prompt = f"""Please act as an expert job reviewer for the semiconductor/VLSI/EDA industry. Read the following job description and evaluate it against the requirements.

Respond ONLY with a valid JSON object matching this exact structure:

### Job Requirements:
{requirements_text}

### Job Details:
Title: {job['title']}
Company: {job['company']}
Location: {job['location']}
URL: {job['url']}

### Job Description:
{cleaned_text[:10000]}

### Instructions:
Return a JSON object with exactly six keys:
- "match": a string, either "yes", "maybe", or "no".
- "reason": a short 1-sentence explanation of your decision.
- "posted_date": a string, the exact date string as it appears in the text (e.g. '3 weeks ago', '12.6.2026', '2026-06-12'). Do NOT calculate relative dates yourself; simply return the raw string. If not found, return 'N/A'.
- "deadline": a string, the deadline for applying formatted strictly as YYYY-MM-DD (e.g. '2026-06-30'). If it is open-ended or 'open until filled', return 'Open until filled'. If not found, return 'N/A'.
- "company": a string, the name of the hiring company as stated in the job posting (e.g. 'Intel' or 'N/A' if not found). Do NOT use the job board name (e.g. do NOT return 'Indeed' or 'LinkedIn' or 'Naukri').
- "location": a string, the city and country of the job. For Indian cities, use the format 'City, India' (e.g. 'Bengaluru, India'). For the Delhi-NCR metro area (Noida, Gurgaon, Greater Noida), return 'Delhi NCR, India'. For US cities, include the state (e.g. 'San Jose, CA, USA'). Return 'N/A' only if truly unknown. If fully remote worldwide, return 'Remote (Worldwide)'.

IMPORTANT: Extract company and location ONLY from information explicitly stated in the job description text. Do NOT guess or hallucinate values.
Do not include any conversational intro/outro or explanations outside the JSON object.
"""
                    try:
                        response_json, llm_used = _call_llm_with_fallback(
                            [{"role": "user", "content": prompt}],
                            llm_endpoint, llm_model, temperature=0.1, max_tokens=500, timeout_local=120,
                        )
                        content = response_json['choices'][0]['message']['content']

                        # Use robust JSON extraction
                        result = extract_json_from_text(content)
                        match = str(result.get("match", "no")).lower()
                        reason = str(result.get("reason", "No reason provided by LLM."))

                        # Only accept AI dates if we don't already have a valid one
                        ai_posted = standardize_date(result.get("posted_date", "N/A"))
                        current_posted = job.get("posted_date", "N/A")
                        if current_posted == "N/A" or not re.match(r'^\d{4}-\d{2}-\d{2}$', str(current_posted)):
                            posted_date = ai_posted
                        else:
                            posted_date = current_posted

                        ai_deadline = standardize_date(result.get("deadline", "N/A"))
                        current_deadline = job.get("deadline", "N/A")
                        if current_deadline == "N/A" or (not re.match(r'^\d{4}-\d{2}-\d{2}$', str(current_deadline)) and 'open' not in str(current_deadline).lower()):
                            deadline = ai_deadline
                        else:
                            deadline = current_deadline

                        # Extract company and location from LLM if scraper had placeholder/unknown
                        ai_company = str(result.get("company", "N/A"))
                        ai_location = str(result.get("location", "N/A"))

                        if ai_company != "N/A" and (job.get('company') in ["Extract from page", "Unknown", "", None]):
                            job['company'] = ai_company
                        if ai_location != "N/A" and (job.get('location') in ["Extract from page", "Unknown", "", None]):
                            job['location'] = ai_location

                        # Regex-based extraction: more reliable than LLM for structured data
                        regex_location = extract_location_from_text(cleaned_text)
                        regex_company = extract_company_from_text(cleaned_text, job.get('title', ''))

                        # Override with regex results if LLM failed or returned placeholder
                        if regex_location and (job.get('location') in ["Extract from page", "Unknown", "", None, "N/A"]):
                            job['location'] = regex_location
                        if regex_company and (job.get('company') in ["Extract from page", "Unknown", "", None, "N/A"]):
                            job['company'] = regex_company

                        if match not in ["yes", "maybe", "no"]:
                            match = "no"
                    except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError, IndexError) as llm_err:
                        match = "error"
                        reason = f"Failed to get or parse LLM response: {llm_err}"

                # Update job dictionary in-place
                job['visited'] = "yes"
                job['matches_requirements'] = match
                job['reason'] = reason
                job['posted_date'] = posted_date
                job['deadline'] = deadline
                job.pop('needs_re_review', None)
                if llm_used:
                    job['eval_model'] = llm_used

                # If a posting matches requirements, save job description text to a file inside job_descriptions/
                if match == 'yes':
                    clean_title = re.sub(r'[^a-zA-Z0-9]', '_', job['title'].lower())[:30]
                    clean_company = re.sub(r'[^a-zA-Z0-9]', '_', job['company'].lower())[:20]
                    url_hash = hashlib.md5(job['url'].encode('utf-8')).hexdigest()[:8]
                    desc_filename = f"{clean_company}_{clean_title}_{url_hash}.txt"

                    desc_dir = os.path.join(BASE_DIR, "job_descriptions")
                    os.makedirs(desc_dir, exist_ok=True)
                    desc_path = os.path.join(desc_dir, desc_filename)
                    try:
                        with open(desc_path, 'w', encoding='utf-8') as f_desc:
                            f_desc.write(f"Title: {job['title']}\n")
                            f_desc.write(f"Company: {job['company']}\n")
                            f_desc.write(f"Location: {job['location']}\n")
                            f_desc.write(f"URL: {job['url']}\n")
                            f_desc.write(f"Posted: {posted_date}\n")
                            f_desc.write(f"Deadline: {deadline}\n")
                            f_desc.write(f"Reason: {reason}\n")
                            f_desc.write("\n" + "=" * 40 + "\n")
                            f_desc.write("JOB DESCRIPTION:\n")
                            f_desc.write("=" * 40 + "\n\n")
                            f_desc.write(text)
                        job['description_file'] = f"job_descriptions/{desc_filename}"
                    except Exception as e_desc:
                        print(f"Error writing description file: {e_desc}")
                        job['description_file'] = None
                else:
                    job['description_file'] = None

                print(f" -> {match.upper()}: {reason} (Posted: {posted_date}, Deadline: {deadline}, Company: {job['company']}, Location: {job['location']}, LLM: {llm_used or 'N/A'})")

            except Exception as e:
                print(f" -> ERROR: Failed to evaluate ({e})")
                job['visited'] = "yes"
                job['matches_requirements'] = "error"
                job['reason'] = "Page load or parsing error."
                job['posted_date'] = "N/A"
                job['deadline'] = "N/A"
                job['description_file'] = None
                job.pop('needs_re_review', None)

            # Save aggressively after each evaluation
            with open(JOBS_FILE, 'w', encoding='utf-8') as f:
                json.dump(jobs, f, indent=2)

        browser.close()


# ─────────────────────────────────────────────────────────────────────────────
# GIT INTEGRATION
# ─────────────────────────────────────────────────────────────────────────────

def update_git():
    """Auto-commit and push changes to GitHub."""
    print("\nUpdating GitHub repository...")
    try:
        repo_dir = os.path.dirname(os.path.abspath(__file__))

        # Clean up the environment to prevent VS Code's git helper from causing socket errors
        env = os.environ.copy()
        env.pop("GIT_ASKPASS", None)
        env["GIT_TERMINAL_PROMPT"] = "0"

        # Check if the folder is inside a Git repository
        is_git = False
        check_path = repo_dir
        while True:
            if os.path.exists(os.path.join(check_path, ".git")):
                is_git = True
                break
            parent = os.path.dirname(check_path)
            if parent == check_path:
                break
            check_path = parent

        if not is_git:
            print("INFO: Directory is not a Git repository. Skipping Git update.")
            return

        # Remove stale index.lock if present (can occur after a timeout or crash)
        lock_file = os.path.join(repo_dir, ".git", "index.lock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                print("INFO: Removed stale .git/index.lock")
            except OSError:
                pass

        # Common kwargs: capture output to prevent pipe deadlocks when run via orchestrator,
        # and close stdin so git never blocks waiting for input.
        git_kw = dict(cwd=repo_dir, env=env, capture_output=True, text=True,
                      stdin=subprocess.DEVNULL)

        # Add updated files
        subprocess.run(["git", "add", "jobs.json", "seen_urls.json", "checkpoint.json",
                        "job_descriptions", "jobs_history.json", "deleted.json"],
                       check=True, timeout=120, **git_kw)
        # Only commit if something was actually staged (git diff --cached avoids false positives
        # from unstaged working-tree changes like .firebase cache or other untracked files)
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                                timeout=30, **git_kw)
        if staged.stdout.strip():
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            commit_message = f"Auto-update scraped jobs: {timestamp}"
            subprocess.run(["git", "commit", "-m", commit_message],
                           check=True, timeout=60, **git_kw)

            # Check for GitHub token in environment variables
            push_cmd = ["git", "push"]
            github_token = os.environ.get("GITHUB_TOKEN")
            if github_token:
                remote_result = subprocess.run(["git", "config", "--get", "remote.origin.url"],
                                               timeout=15, **git_kw)
                remote_url = remote_result.stdout.strip()
                if remote_url.startswith("https://"):
                    auth_url = remote_url.replace("https://", f"https://{github_token}@")
                    push_cmd = ["git", "push", auth_url]

            try:
                subprocess.run(push_cmd, check=True, timeout=120, **git_kw)
                print("Successfully pushed updates to GitHub!")
            except subprocess.CalledProcessError:
                print("Failed to push to GitHub (Check your GITHUB_TOKEN or internet connection).")
        else:
            print("No changes to commit. GitHub is already up to date.")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"Failed to update Git: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# FIREBASE CLOUD SYNC
# ─────────────────────────────────────────────────────────────────────────────

def poll_firebase_feedback():
    """Polls the Firebase Firestore REST API for user feedback, updates requirements, and marks them read."""
    url = "https://firestore.googleapis.com/v1/projects/priya-jobs-dashboard/databases/(default)/documents/user_feedback"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return  # Database not created, or empty, or permission denied

        data = response.json()
        documents = data.get("documents", [])
        if not documents:
            return

        print(f"\nINFO: Found {len(documents)} new feedback items from the cloud dashboard!")

        new_positive_rules = []
        new_negative_rules = []
        user_review_updates = {}
        match_updates = {}
        processed_urls = set()

        for doc in documents:
            doc_name = doc.get("name")
            fields = doc.get("fields", {})
            status = fields.get("status", {}).get("stringValue", "unread")

            if status == "read":
                continue

            feedback_type = fields.get("type", {}).get("stringValue", "negative")
            url_field = fields.get("url", {}).get("stringValue", "")
            if url_field:
                processed_urls.add(url_field)

            if feedback_type == "user_review_update":
                new_status = fields.get("user_review", {}).get("stringValue", "pending")
                url_field = fields.get("url", {}).get("stringValue", "")
                if url_field:
                    user_review_updates[url_field] = new_status

                # Update status to "read"
                if doc_name:
                    update_url = f"https://firestore.googleapis.com/v1/{doc_name}?updateMask.fieldPaths=status"
                    payload = {"fields": {"status": {"stringValue": "read"}}}
                    requests.patch(update_url, json=payload, timeout=10)
                continue


            if feedback_type == "applied_update":
                new_status = fields.get("applied", {}).get("stringValue", "no")
                url_field = fields.get("url", {}).get("stringValue", "")
                if url_field:
                    if 'applied_updates' not in locals():
                        applied_updates = {}
                    applied_updates[url_field] = new_status
                
                # Update status to "read"
                if doc_name:
                    update_url = f"https://firestore.googleapis.com/v1/{doc_name}?updateMask.fieldPaths=status"
                    payload = {"fields": {"status": {"stringValue": "read"}}}
                    requests.patch(update_url, json=payload, timeout=10)
                continue

            reason = fields.get("reason", {}).get("stringValue", "")

            if feedback_type == "positive":
                if url_field:
                    match_updates[url_field] = "yes"
            else:
                if url_field:
                    match_updates[url_field] = "no"

            if reason and reason.strip():
                if feedback_type == "positive":
                    new_positive_rules.append(reason.strip())
                else:
                    new_negative_rules.append(reason.strip())

            # Update the document status to "read" so we keep a history in the cloud
            if doc_name:
                update_url = f"https://firestore.googleapis.com/v1/{doc_name}?updateMask.fieldPaths=status"
                payload = {"fields": {"status": {"stringValue": "read"}}}
                requests.patch(update_url, json=payload, timeout=10)

        if new_positive_rules or new_negative_rules:
            with open(REQ_FILE, 'a', encoding='utf-8') as f:
                if new_negative_rules:
                    f.write("\n\n### Automatically Added Negative Constraints (from UI Rejections):\n")
                    for rule in new_negative_rules:
                        f.write(f"- NEGATIVE CONSTRAINT: The user explicitly rejected a previous job because: '{rule}'. Do NOT match jobs that have this issue.\n")
                if new_positive_rules:
                    f.write("\n\n### Automatically Added Positive Constraints (from UI Approvals):\n")
                    for rule in new_positive_rules:
                        f.write(f"- POSITIVE CONSTRAINT: The user explicitly approved a previous job because: '{rule}'. Make sure to MATCH jobs that have this characteristic.\n")
            print(f"INFO: Successfully updated job_requirements.md with {len(new_positive_rules)} positive and {len(new_negative_rules)} negative rules!")


        if locals().get('applied_updates'):
            try:
                if os.path.exists(JOBS_FILE):
                    with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                        jobs = json.load(f)
                    changed = False
                    for j in jobs:
                        if j.get('url') in applied_updates:
                            j['applied'] = applied_updates[j['url']]
                            changed = True
                    if changed:
                        with open(JOBS_FILE, 'w', encoding='utf-8') as f:
                            json.dump(jobs, f, indent=2)
                    print(f"INFO: Successfully synced applied status for {len(applied_updates)} jobs from the cloud.")
            except Exception as e:
                print(f"Error syncing applied status: {e}")

        if user_review_updates:
            try:
                if os.path.exists(JOBS_FILE):
                    with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                        jobs = json.load(f)
                    changed = False
                    for j in jobs:
                        if j.get('url') in user_review_updates:
                            j['user_review'] = user_review_updates[j['url']]
                            changed = True
                    if changed:
                        with open(JOBS_FILE, 'w', encoding='utf-8') as f:
                            json.dump(jobs, f, indent=2)
                    print(f"INFO: Successfully synced user_review status for {len(user_review_updates)} jobs from the cloud.")
            except Exception as e:
                print(f"Error syncing user review status: {e}")

    
        if match_updates:
            try:
                if os.path.exists(JOBS_FILE):
                    with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                        jobs = json.load(f)
                    changed = False
                    for j in jobs:
                        if j.get('url') in match_updates:
                            j['matches_requirements'] = match_updates[j['url']]
                            changed = True
                    if changed:
                        with open(JOBS_FILE, 'w', encoding='utf-8') as f:
                            json.dump(jobs, f, indent=2)
                    print(f"INFO: Successfully synced matches_requirements for {len(match_updates)} jobs from the cloud.")
            except Exception as e:
                print(f"Error syncing match updates: {e}")
        
        # Wipe shared_state since all updates are now safely in jobs.json
        if user_review_updates or match_updates:
            try:
                # Get the correct project ID based on the URL we polled
                proj_id = "priya-jobs-dashboard"
                # wait, let's just use the url from the top of the function
                wipe_url = url.replace('user_feedback', 'shared_state/job_status')
                requests.patch(wipe_url, json={"fields": {}}, timeout=10)
                print("INFO: Cleared shared_state temporary queue.")
            except Exception as e:
                print(f"Error clearing shared_state: {e}")

                
    except Exception as e:
        print(f"Error polling Firebase feedback: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# DATE SELF-HEALING
# ─────────────────────────────────────────────────────────────────────────────

def self_heal_dates():
    """Run through existing jobs in jobs.json and standardize their dates using the native scraper logic."""
    if not os.path.exists(JOBS_FILE):
        return
    try:
        with open(JOBS_FILE, 'r', encoding='utf-8') as f:
            jobs = json.load(f)

        changed = False
        for job in jobs:
            p = str(job.get('posted_date', ''))
            d = str(job.get('deadline', ''))

            new_p = standardize_date(p)
            new_d = standardize_date(d)

            if new_p != p:
                job['posted_date'] = new_p
                changed = True
            if new_d != d:
                job['deadline'] = new_d
                changed = True

        if changed:
            print("INFO: Natively standardized messy dates in jobs.json!")
            with open(JOBS_FILE, 'w', encoding='utf-8') as f:
                json.dump(jobs, f, indent=2)
    except Exception as e:
        print(f"Error self-healing dates: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# STATUS & CONTROL
# ─────────────────────────────────────────────────────────────────────────────

# Event flag to signal when the user wants to stop
stop_event = threading.Event()


def listen_for_input():
    """Background task waiting for the user to press Enter."""
    try:
        input()
        stop_event.set()
    except EOFError:
        pass


def print_job_summary():
    """Reads jobs.json and prints a summary of job statuses."""
    if not os.path.exists(JOBS_FILE):
        print("\nStats - No jobs.json file found.")
        return

    try:
        with open(JOBS_FILE, 'r', encoding='utf-8') as f:
            jobs_data = json.load(f)
            total_jobs = len(jobs_data)
            matching_jobs = sum(1 for j in jobs_data if j.get('matches_requirements') == 'yes')
            maybe_jobs = sum(1 for j in jobs_data if j.get('matches_requirements') == 'maybe')
            no_jobs = sum(1 for j in jobs_data if j.get('matches_requirements') == 'no')
            pending_jobs = sum(1 for j in jobs_data if j.get('matches_requirements') == 'pending')
            re_review_jobs = sum(1 for j in jobs_data if j.get('needs_re_review') is True)

        re_review_str = f" | Re-review: {re_review_jobs}" if re_review_jobs else ""
        print(f"\nStats - Total jobs: {total_jobs} | Yes Match: {matching_jobs} | Maybe Match: {maybe_jobs} | No Match: {no_jobs} | Pending: {pending_jobs}{re_review_str}")
    except Exception as e:
        print(f"Error reading jobs file for status display: {e}")


FIRESTORE_BASE = "https://firestore.googleapis.com/v1/projects/priya-jobs-dashboard/databases/(default)/documents"


def poll_re_review_request():
    """Check Firebase for a user-triggered re-review request and run it synchronously."""
    doc_url = f"{FIRESTORE_BASE}/shared_state/re_review_request"
    try:
        response = requests.get(doc_url, timeout=10)
        if response.status_code == 404:
            return  # document not created yet — no re-review requested
        if response.status_code != 200:
            print(f"INFO: poll_re_review: Firestore GET returned {response.status_code} — skipping.")
            return
        data = response.json()
        fields = data.get("fields", {})
        status = fields.get("status", {}).get("stringValue", "")
        if status != "requested":
            return

        print("INFO: " + "=" * 60)
        print("INFO: RE-REVIEW TRIGGERED BY USER (dashboard button)")
        requests.patch(
            f"{doc_url}?updateMask.fieldPaths=status",
            json={"fields": {"status": {"stringValue": "in_progress"}}},
            timeout=10
        )

        by_status = {}
        skip_done = 0
        skip_applied = 0
        eligible = {'yes', 'maybe', 'pending', 'error'}

        if os.path.exists(JOBS_FILE):
            with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                jobs = json.load(f)
            for job in jobs:
                if job.get('user_review') == 'done':
                    skip_done += 1
                    continue
                if job.get('applied') == 'yes':
                    skip_applied += 1
                    continue
                s = job.get('matches_requirements', '')
                if s in eligible:
                    job['needs_re_review'] = True
                    by_status[s] = by_status.get(s, 0) + 1
            with open(JOBS_FILE, 'w', encoding='utf-8') as f:
                json.dump(jobs, f, indent=2)

        total = sum(by_status.values())
        scope_parts = [f"{s}={n}" for s, n in sorted(by_status.items())]
        print(f"INFO: Scope     : {' | '.join(scope_parts) if scope_parts else 'nothing to review'} → {total} job(s) queued")
        print(f"INFO: Skipping  : user_review=done ({skip_done})  applied=yes ({skip_applied})")
        print("INFO: " + "=" * 60)

        while not stop_event.is_set():
            with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                jobs = json.load(f)
            batch = [j for j in jobs if j.get('needs_re_review') == True]
            if not batch:
                break
            review_pending_jobs(specific_urls={j['url'] for j in batch[:15]})

        completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        requests.patch(
            f"{doc_url}?updateMask.fieldPaths=status&updateMask.fieldPaths=completedAt",
            json={"fields": {
                "status": {"stringValue": "completed"},
                "completedAt": {"stringValue": completed_at}
            }},
            timeout=10
        )
        print("INFO: " + "=" * 60)
        print(f"INFO: RE-REVIEW COMPLETE at {completed_at}  ({total} job(s) reviewed)")
        print("INFO: " + "=" * 60)
    except Exception as e:
        print(f"Error handling re-review request: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global _tee_logger
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_path = os.path.join(LOGS_DIR, f"scraper_{datetime.now().strftime('%Y%m%d')}.log")
    _tee_logger = TeeLogger(log_path)
    sys.stdout = _tee_logger

    generate_history_from_backups()
    self_heal_dates()
    parser = argparse.ArgumentParser(description="Semiconductor Job Scraper and Reviewer")
    parser.add_argument("--git-only", action="store_true", help="Only run the Git commit and push step, then exit.")
    parser.add_argument("--review-only", action="store_true", help="Only run the local LLM review step on pending jobs, then exit.")
    parser.add_argument("--review-urls", nargs="+", metavar="URL", help="Review only these specific job URL(s) (must already exist as 'pending' in jobs.json), then exit.")
    parser.add_argument("--scrape-only", action="store_true", help="Only run the scraping step, then exit.")
    parser.add_argument("--max-jobs", type=int, default=200, help="Maximum number of new jobs to fetch in this run (default 200). Use 0 for unlimited.")
    args = parser.parse_args()

    if args.git_only:
        update_git()
        return

    if args.review_urls:
        target_urls = set(args.review_urls)
        print(f"INFO: Reviewing {len(target_urls)} specific URL(s)...")
        review_pending_jobs(specific_urls=target_urls)
        moved_count = clean_blocked_jobs()
        with open(JOBS_FILE, 'r', encoding='utf-8') as f:
            latest_jobs = json.load(f)
        save_history_snapshot(latest_jobs, deleted=moved_count)
        update_git()
        print_job_summary()
        return

    if args.review_only:
        check_requirements_update()
        while True:
            if not os.path.exists(JOBS_FILE):
                break
            with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                jobs = json.load(f)

            pending_urls = [j['url'] for j in jobs if j.get('matches_requirements') == 'pending' or j.get('needs_re_review') == True]
            if not pending_urls:
                print("INFO: No more pending jobs to review.")
                break

            batch_urls = pending_urls[:15]
            print(f"\nINFO: Reviewing batch of {len(batch_urls)} pending jobs (Remaining pending: {len(pending_urls)})...")
            review_pending_jobs(specific_urls=set(batch_urls))

            clean_blocked_jobs()
            update_git()
            print_job_summary()
            time.sleep(1)
        return

    if args.scrape_only:
        check_requirements_update()
        targets = generate_targets()
        total_targets = len(targets)
        visited_indices = set()

        while len(visited_indices) < total_targets:
            checkpoint_idx = 0
            if os.path.exists(CHECKPOINT_FILE):
                try:
                    with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                        checkpoint_idx = json.load(f).get("target_index", 0)
                except Exception:
                    pass

            if checkpoint_idx >= total_targets or checkpoint_idx < 0:
                checkpoint_idx = 0

            print(f"\nINFO: Scraping batch of up to 15 jobs (Starting target index: {checkpoint_idx}/{total_targets})...")
            if _tee_logger:
                _tee_logger.start_capture()
            new_jobs = scrape_all_jobs(max_jobs=15)
            if _tee_logger:
                _tee_logger.stop_capture()
                if _checkpoint_reached_end():
                    analyze_scrape_run_log(_tee_logger.flush_cycle())

            new_checkpoint_idx = 0
            if os.path.exists(CHECKPOINT_FILE):
                try:
                    with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                        new_checkpoint_idx = json.load(f).get("target_index", 0)
                except Exception:
                    pass

            if new_checkpoint_idx > checkpoint_idx:
                for i in range(checkpoint_idx, new_checkpoint_idx):
                    visited_indices.add(i)
            else:
                for i in range(checkpoint_idx, total_targets):
                    visited_indices.add(i)
                for i in range(0, new_checkpoint_idx):
                    visited_indices.add(i)

            clean_blocked_jobs()
            update_git()

            if not new_jobs and new_checkpoint_idx == checkpoint_idx:
                print("INFO: No progress made, stopping scrape loop.")
                break

            time.sleep(1)
        return

    # Self-heal dates before main loop
    self_heal_dates()

    print("INFO: Scraper script starting execution loop...")
    # Start the background thread to listen for user input
    input_thread = threading.Thread(target=listen_for_input, daemon=True)
    input_thread.start()

    while not stop_event.is_set():
        try:
            poll_firebase_feedback()
        except Exception as e:
            print(f"Error polling firebase: {e}")

        try:
            poll_re_review_request()
        except Exception as e:
            print(f"Error handling re-review request: {e}")

        try:
            check_requirements_update()
        except Exception as e:
            print(f"An error occurred checking requirements: {e}")

        # 1. Gather all pending jobs
        pending_jobs = []
        if os.path.exists(JOBS_FILE):
            try:
                with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                    jobs_data = json.load(f)
                    pending_jobs = [j for j in jobs_data if j.get('matches_requirements') in ['pending', 'error'] or j.get('needs_re_review') == True]
            except Exception as e:
                print(f"Error reading jobs file: {e}")

        if pending_jobs:
            # We have pending jobs, flush a batch of them first
            print(f"\nINFO: Flushing pending jobs first. {len(pending_jobs)} pending jobs remaining.")
            batch_urls = [j['url'] for j in pending_jobs[:15]]
            try:
                review_pending_jobs(specific_urls=set(batch_urls))
            except Exception as e:
                print(f"An error occurred during reviewing: {e}")

            try:
                moved_count = clean_blocked_jobs()
                with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                    latest_jobs = json.load(f)
                save_history_snapshot(latest_jobs, deleted=moved_count)
                update_git()
            except Exception as e:
                print(f"An error occurred during Git update: {e}")

            print_job_summary()
            print("Waiting 5 seconds before moving to scrape new jobs. Press [Enter] or Ctrl+C to stop...")

            slept = 0
            while slept < 5 and not stop_event.is_set():
                time.sleep(0.5)
                slept += 0.5

            if stop_event.is_set():
                print("Stopping the scraper...")
                break
            continue

        quota = 15
        new_jobs = []

        try:
            print(f"\nINFO: Scanning for up to {quota} new unseen jobs...")
            if _tee_logger:
                _tee_logger.start_capture()
            new_jobs = scrape_all_jobs(max_jobs=quota)
            if _tee_logger:
                _tee_logger.stop_capture()
                if _checkpoint_reached_end():
                    analyze_scrape_run_log(_tee_logger.flush_cycle())
        except Exception as e:
            if _tee_logger:
                _tee_logger.stop_capture()
            print(f"An error occurred during scraping: {e}")

        # Collect URLs to review
        urls_to_review = [j['url'] for j in new_jobs]

        if urls_to_review:
            print(f"INFO: Reviewing {len(urls_to_review)} jobs in this batch (New: {len(new_jobs)}, Existing Pending: {len(urls_to_review) - len(new_jobs)})")
            try:
                review_pending_jobs(specific_urls=set(urls_to_review))
            except Exception as e:
                print(f"An error occurred during reviewing: {e}")
        else:
            print("INFO: No jobs found to review in this iteration.")

        try:
            moved_count = clean_blocked_jobs()
            with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                latest_jobs = json.load(f)
            save_history_snapshot(latest_jobs, deleted=moved_count)
            update_git()
        except Exception as e:
            print(f"An error occurred during Git update: {e}")

        print_job_summary()

        # Determine wait time
        wait_time = 5
        if not new_jobs and len(pending_jobs) <= 15:
            wait_time = 600
            print(f"\nINFO: No new unseen jobs found in this iteration. Increasing wait time to {wait_time} seconds (10 mins).")

        print(f"Waiting {wait_time} seconds before the next run. Press [Enter] or Ctrl+C to stop...")

        # This loop waits in small increments so KeyboardInterrupt can be caught immediately on Windows
        slept = 0
        while slept < wait_time and not stop_event.is_set():
            time.sleep(0.5)
            slept += 0.5

        if stop_event.is_set():
            print("Stopping the scraper...")
            break

if __name__ == "__main__":
    scraper_lock = FileLock(SCRAPER_LOCK_FILE, timeout=1)
    try:
        scraper_lock.acquire()
    except Timeout:
        print(f"ERROR: Another scraper.py instance already holds {SCRAPER_LOCK_FILE}. "
              f"Refusing to start a second instance (concurrent runs corrupt jobs_history.json and git state).")
        sys.exit(1)

    try:
        try:
            main()
        except KeyboardInterrupt:
            print("\nINFO: Scraper stopped by user (Ctrl+C).")
            stop_event.set()
    finally:
        scraper_lock.release()
