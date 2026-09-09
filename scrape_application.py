#!/usr/bin/env python3
"""
Scrape job application form questions from a job listing URL.

First run per platform: prompts for credentials interactively, saves to PRIVATE/.env,
saves Playwright session cookies. All subsequent runs are fully automatic.

Usage:
    python scrape_application.py --job-url URL --job-id ID --out-dir DIR

Output:
    {out-dir}/{job_id}_questions.json
"""

import os
import re
import sys
import json
import time
import base64
import getpass
import argparse
import requests
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from html import unescape as html_unescape
import anthropic
from playwright.sync_api import sync_playwright

# ── Paths (defaults; overridden by --private-dir at runtime) ──────────────────
# Prefer PRIYA_PRIVATE_DIR env var so the script works on any machine.

PRIVATE_DIR = Path(os.environ.get("PRIYA_PRIVATE_DIR", str(Path(__file__).parent.parent / "Priya_jobs_private")))
ENV_FILE    = PRIVATE_DIR / ".env"
SESSION_DIR = PRIVATE_DIR / "sessions"

PLATFORM_PATTERNS = {
    "linkedin":   ["linkedin.com"],
    "indeed":     ["indeed.com"],
    "duunitori":  ["duunitori.fi"],
    "greenhouse": ["boards.greenhouse.io", "boards.eu.greenhouse.io"],
    "lever":      ["jobs.lever.co"],
    "workday":    ["myworkdayjobs.com"],
}

CRED_MAP = {
    "linkedin": ("LINKEDIN_EMAIL", "LINKEDIN_PASSWORD", "LinkedIn"),
    "indeed":   ("INDEED_EMAIL",   "INDEED_PASSWORD",   "Indeed"),
    "workday":  ("WORKDAY_EMAIL",  "WORKDAY_PASSWORD",  "Workday"),
}

# Platforms that support Google OAuth instead of email+password.
# Set INDEED_AUTH_METHOD=google in .env (or pass --google-auth) to use.
GOOGLE_AUTH_PLATFORMS = {"indeed"}

# ── .env helpers ──────────────────────────────────────────────────────────────

def load_env_file():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def save_env_var(key, value):
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    updated = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f'{key}="{value}"'
            updated = True
            break
    if not updated:
        lines.append(f'{key}="{value}"')
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value
    print(f"  Saved {key} to {ENV_FILE}")


# ── Credential prompting ───────────────────────────────────────────────────────

def is_google_auth(platform):
    """Return True if this platform is configured to use Google OAuth."""
    if platform not in GOOGLE_AUTH_PLATFORMS:
        return False
    method = os.environ.get(f"{platform.upper()}_AUTH_METHOD", "").lower()
    return method == "google"


def ensure_credentials(platform):
    """Return (email, password). Prompts and saves if not already set.
    Returns (None, None) for platforms using Google OAuth."""
    if platform not in CRED_MAP:
        return None, None
    if is_google_auth(platform):
        return None, None
    email_key, pass_key, label = CRED_MAP[platform]
    email = os.environ.get(email_key)
    password = os.environ.get(pass_key)
    if not email or not password:
        print(f"\n[{label}] First-time setup — credentials saved to {ENV_FILE}")
        print("  They won't be asked again after this.")
        if platform == "indeed":
            print("  (Tip: set INDEED_AUTH_METHOD=google in .env to use Google login instead)")
        print()
    if not email:
        email = input(f"  {label} email: ").strip()
        save_env_var(email_key, email)
    if not password:
        password = getpass.getpass(f"  {label} password: ")
        save_env_var(pass_key, password)
    return email, password


# ── Platform detection ─────────────────────────────────────────────────────────

def detect_platform(url):
    for platform, patterns in PLATFORM_PATTERNS.items():
        for p in patterns:
            if p in url:
                return platform
    return "generic"


# ── Session management ─────────────────────────────────────────────────────────

def session_file(platform):
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_DIR / f"{platform}_session.json"


def session_file_for_domain(url: str) -> Path:
    """Return a per-domain session file path for non-platform portals."""
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        domain = "unknown"
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_DIR / f"domain_{domain}.json"


def try_load_domain_session(context, url: str) -> bool:
    """Load previously saved cookies for the domain of `url`. Returns True if loaded."""
    sess = session_file_for_domain(url)
    if not sess.exists():
        return False
    try:
        state = json.loads(sess.read_text(encoding="utf-8"))
        cookies = state.get("cookies", [])
        if cookies:
            context.add_cookies(cookies)
            print(f"  Loaded saved session for {urlparse(url).netloc}")
            return True
    except Exception:
        pass
    return False


ALREADY_APPLIED_SIGNALS = [
    "applied on", "you've already applied", "you have already applied",
    "application submitted", "already applied", "application received",
    "your application has been received", "you applied on",
    "hakemuksesi on vastaanotettu", "olet jo hakenut", "hakemus lähetetty",
    "application was submitted", "successfully applied",
]

EXPIRED_LISTING_SIGNALS = [
    "tämä työpaikkailmoitus indeedissä on vanhentunut",
    "tämä ilmoitus on vanhentunut",
    "ilmoitus on poistettu",
    "this job listing has expired",
    "this indeed job listing has expired",
    "this job is no longer available",
    "job posting is no longer available",
    "posting has been removed",
    "this job posting on indeed is outdated",
    "job posting is outdated",
    "is no longer accepting job applications",
    "not currently actively recruiting",
]

_scrape_flags: dict = {"expired": False, "login_wall": False}

FIRESTORE_DOC_URL = (
    "https://firestore.googleapis.com/v1/projects/priya-jobs-dashboard"
    "/databases/(default)/documents/shared_state/job_status"
)


def detect_already_applied(page) -> tuple:
    """Return (True, date_str) if the portal shows this job was already applied for."""
    try:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        for signal in ALREADY_APPLIED_SIGNALS:
            if signal in text:
                date_match = re.search(
                    r'applied\s+on\s+([a-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[a-z]+\s+\d{4})',
                    text,
                )
                date_str = date_match.group(1).title() if date_match else ""
                return True, date_str
    except Exception:
        pass
    return False, ""


def detect_expired_listing(page) -> bool:
    """Return True if the page shows an expired/removed job listing notice."""
    try:
        text = (page.evaluate("() => document.body.innerText") or "").lower()
        return any(s in text for s in EXPIRED_LISTING_SIGNALS)
    except Exception:
        return False


def _is_applied_in_firestore(job_url: str) -> bool:
    """Check Firestore to see if this job URL has applied='yes'."""
    try:
        resp = requests.get(FIRESTORE_DOC_URL, timeout=10)
        resp.raise_for_status()
        fields = resp.json().get("fields", {})
        if job_url in fields:
            entry = fields[job_url]
            if "mapValue" in entry:
                applied = entry["mapValue"].get("fields", {}).get("applied", {})
                return applied.get("stringValue") == "yes"
    except Exception:
        pass
    return False


def move_job_to_deleted(job_id: str, reason: str = "Expired job listing") -> bool:
    """
    Move a job from jobs.json to deleted.json.
    No-op if the job is marked applied='yes' in either jobs.json or Firestore.
    Returns True if the job was moved.
    """
    jobs_path = _find_jobs_json()
    if not jobs_path:
        print("  (Cannot find jobs.json — skipping deletion)")
        return False

    try:
        jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  (Failed to read jobs.json: {e})")
        return False

    target = next((j for j in jobs if j.get("id") == job_id), None)
    if not target:
        print(f"  (job_id {job_id!r} not found in jobs.json)")
        return False

    # Guard: don't move if already applied
    if target.get("applied") == "yes":
        print(f"  Applied job {job_id} — not moving to deleted.json")
        return False
    if _is_applied_in_firestore(target.get("url", "")):
        print(f"  Applied job {job_id} (Firestore) — not moving to deleted.json")
        return False

    target["deletion_reason"] = reason
    remaining = [j for j in jobs if j.get("id") != job_id]

    deleted_path = jobs_path.parent / "deleted.json"
    deleted: list = []
    if deleted_path.exists():
        try:
            deleted = json.loads(deleted_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    seen_urls = {j.get("url") for j in deleted}
    if target.get("url") not in seen_urls:
        deleted.append(target)

    jobs_path.write_text(json.dumps(remaining, indent=2, ensure_ascii=False), encoding="utf-8")
    deleted_path.write_text(json.dumps(deleted, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Job {job_id} moved to deleted.json (reason: {reason})")
    return True


def _find_jobs_json() -> Path | None:
    """Auto-discover jobs.json in a sibling of PRIVATE_DIR."""
    parent = PRIVATE_DIR.parent
    try:
        for d in sorted(parent.iterdir()):
            if d.is_dir() and (d / "jobs.json").exists():
                return d / "jobs.json"
    except Exception:
        pass
    return None


def mark_job_applied_firestore(job_id: str, applied_date: str = "") -> bool:
    """
    Look up the job URL from jobs.json using job_id, then write applied='yes'
    (and optionally applied_date) to Firestore. Returns True on success.
    """
    jobs_json = _find_jobs_json()
    if not jobs_json:
        print("  (Cannot find jobs.json — Firestore update skipped)")
        return False
    try:
        jobs = json.loads(jobs_json.read_text(encoding="utf-8"))
        job_url = next((j["url"] for j in jobs if j.get("id") == job_id), None)
    except Exception as e:
        print(f"  (Failed to read jobs.json: {e})")
        return False
    if not job_url:
        print(f"  (job_id {job_id!r} not found in jobs.json — Firestore update skipped)")
        return False

    def deserialize(v):
        if "stringValue"  in v: return v["stringValue"]
        if "booleanValue" in v: return v["booleanValue"]
        if "mapValue"     in v:
            return {k: deserialize(fv) for k, fv in v["mapValue"].get("fields", {}).items()}
        return None

    def serialize(val):
        if val is None:          return {"nullValue": None}
        if isinstance(val, bool): return {"booleanValue": val}
        if isinstance(val, str):  return {"stringValue": val}
        if isinstance(val, dict):
            return {"mapValue": {"fields": {k: serialize(v) for k, v in val.items()}}}
        return {"stringValue": str(val)}

    try:
        resp = requests.get(FIRESTORE_DOC_URL, timeout=20)
        resp.raise_for_status()
        current = {k: deserialize(v) for k, v in resp.json().get("fields", {}).items()}

        entry = current.get(job_url, {})
        if not isinstance(entry, dict):
            entry = {}
        entry["applied"] = "yes"
        if applied_date:
            entry["applied_date"] = applied_date
        current[job_url] = entry

        body = {"fields": {k: serialize(v) for k, v in current.items()}}
        requests.patch(FIRESTORE_DOC_URL, json=body, timeout=20).raise_for_status()
        return True

    except Exception as e:
        print(f"  (Firestore update failed: {e})")
        return False


LOGIN_WALL_URL_SIGNALS = {
    "/login", "/signin", "/sign-in", "/register", "/registration",
    "/auth/", "/account/new", "/candidate/login", "/user/login",
    "/kirjaudu", "/rekisteroidy",
}

# Signals that a URL is on an employer's own ATS/careers portal rather than a job board.
# Pages on these domains that aren't recognised as forms likely need login or navigation.
EMPLOYER_PORTAL_SIGNALS = {
    "careerhub", "careers.", "talent.", "jobs.", "recruit.",
    "greenhouse.io", "lever.co", "workday", "successfactors",
    "teamtailor", "recruitee", "personio", "ashby", "workable",
    "jobvite", "icims", "taleo", "breezy", "bamboohr",
}


def detect_login_wall(page) -> bool:
    """Return True if the current page is a login or registration wall."""
    url = page.url.lower()
    if any(s in url for s in LOGIN_WALL_URL_SIGNALS):
        return True
    try:
        has_password = page.evaluate(
            '() => document.querySelector(\'input[type="password"]\') !== null'
        )
        if has_password:
            return True
    except Exception:
        pass
    return False


def is_employer_portal(url: str) -> bool:
    """
    Return True if the URL appears to be on an employer's own ATS or careers portal
    (not a Finnish job board).  Used to decide whether to offer navigation assistance.
    """
    url_lower = url.lower()
    if any(d in url_lower for d in LISTING_DOMAINS):
        return False
    return any(s in url_lower for s in EMPLOYER_PORTAL_SIGNALS)


def navigate_through_login(page, context, target_url: str, non_interactive: bool = False) -> bool:
    """
    Handle being stuck on a login wall OR on an employer portal page that isn't a form.
    Tries saved session first (silent).  If that doesn't work, prompts the user to log
    in / create an account / navigate to the form in the visible browser window.

    Returns False immediately if the current page is still on a known job board
    (no user assistance makes sense there).
    Returns True after the user confirms they're ready, having saved the session.

    After this returns True the caller should check is_application_form(page) on the
    CURRENT page (user may have navigated there), then fall back to target_url if needed.

    When non_interactive=True (unattended runs), never blocks on input(): if the saved
    session doesn't clear a login wall, it records that in _scrape_flags["login_wall"]
    and returns False so the caller can fall through to its normal "0 questions" path
    instead of hanging forever waiting for a human who isn't there.
    """
    url = page.url
    url_lower = url.lower()

    # If we're still on a job board, don't prompt — nothing to help with here
    if any(d in url_lower for d in LISTING_DOMAINS):
        return False

    is_login  = detect_login_wall(page)
    is_portal = is_employer_portal(url)

    if not is_login and not is_portal:
        return False

    domain = urlparse(url).netloc

    if is_login:
        print(f"  Login wall detected at: {domain}")
    else:
        print(f"  Employer portal detected (navigation assistance): {domain}")

    # Try previously saved session first (silent, no user input)
    if try_load_domain_session(context, url):
        try:
            page.reload()
            time.sleep(2)
        except Exception:
            pass
        if not detect_login_wall(page):
            print(f"  Saved session restored for {domain}")
            return True

    if non_interactive:
        if is_login:
            print(f"  Login wall not cleared by saved session — flagging for manual account setup: {domain}")
            _scrape_flags["login_wall"] = True
        else:
            print(f"  Navigation assistance needed (unattended run, skipping): {domain}")
        return False

    # Need the user's help
    print()
    print("  ---------------------------------------------------------")
    if is_login:
        print(f"  Login / registration required: {domain}")
        print()
        print("  Please log in or create an account in the open browser window.")
    else:
        print(f"  Navigation assistance needed: {domain}")
        print()
        print("  Could not automatically reach the application form.")
        print("  Please navigate to it in the open browser window.")
    print(f"  Target: {target_url[:120]}")
    print()
    print("  Steps:")
    print("    1. Log in or create an account if required")
    print("    2. Navigate to the application form (click 'Apply' if needed)")
    print("    3. Wait until the form fields are visible on screen")
    print("  ---------------------------------------------------------")
    input("  Press Enter once you are on the application form: ")

    # Save session so future runs on this domain are automatic
    try:
        context.storage_state(path=str(session_file_for_domain(url)))
        print(f"  Session saved for {domain}")
    except Exception as e:
        print(f"  (Could not save session: {e})")

    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass

    return True


def needs_relogin(page, platform):
    url = page.url
    if platform == "linkedin":
        return any(k in url for k in ("login", "authwall", "checkpoint", "uas/login"))
    if platform == "indeed":
        return "login" in url or "signin" in url
    return False


# ── Cookie-label filter ───────────────────────────────────────────────────────

COOKIE_LABEL_KEYWORDS = {
    "evästeet", "eväste", "cookie", "cookies", "consent",
    "välttämättömät", "kolmansien osapuolien", "third-party",
    "tietosuoja", "yksityisyydensuoja", "cookiebanner",
}

# Labels that strongly suggest an actual application form field
FORM_LABEL_HINTS = {
    # Name fields — application-specific (not present on most non-form pages)
    "first name", "last name", "full name", "given name", "surname",
    "etunimi", "sukunimi",
    # Phone — application-specific
    "phone", "mobile", "telephone", "puhelin",
    # Application documents — highly specific
    "cover letter", "covering letter", "motivation", "motivaatio",
    "cv", "resume", "curriculum vitae", "portfolio",
    "motivaatiokirje", "saatekirje", "ansioluettelo",
    # Job-application meta fields
    "linkedin", "salary", "palkkatoive", "availability", "start date",
    "notice period", "irtisanomisaika",
    # NOTE: "email"/"e-mail"/"nimi"/"sähköposti"/"website" removed — too generic (matches newsletter/alert signups)
}

# Submit-button text confirming an application action.
# Only include multi-word / form-specific phrases — single words like "apply",
# "send", "lähetä", "hae" are too generic and match navigation CTAs on listing pages.
APPLY_SUBMIT_HINTS = {
    "submit application", "jätä hakemus", "lähetä hakemus", "tallenna hakemus",
    "send application", "submit your application",
}

# Submit-button text that signals a search bar (false positive to exclude)
SEARCH_SUBMIT_HINTS = {"search", "etsi", "hae töitä", "find jobs"}

# Known job listing / search domains — pages on these should NOT be treated as forms
# unless the URL also contains an explicit apply-path signal.
LISTING_DOMAINS = {
    "fi.indeed.com", "indeed.com", "duunitori.fi", "jobly.fi",
    "mol.fi", "te-palvelut.fi", "oikotie.fi", "monster.com",
    "glassdoor.com", "linkedin.com/jobs",
}

def is_cookie_label(text):
    norm = text.lower()
    if any(kw in norm for kw in COOKIE_LABEL_KEYWORDS):
        return True
    if text.count("\n") > 3:   # cookie toggle widgets have many embedded newlines
        return True
    return False


# ── Form extraction helpers ───────────────────────────────────────────────────

def extract_form_fields(page, scope=None):
    """Extract form fields. Pass scope=ElementHandle to restrict to a section."""
    root = scope if scope else page
    seen = set()
    questions = []

    def add(label, ftype, options=None, required=False, accept=""):
        label = label.strip()
        if not label or label in seen or len(label) > 400:
            return
        if is_cookie_label(label):
            return
        seen.add(label)
        questions.append({
            "label": label,
            "type": ftype,
            "options": options or [],
            "required": required,
            "accept": accept,
        })

    # Labels → associated fields
    for label_el in root.query_selector_all("label"):
        text = label_el.inner_text().strip()
        for_id = label_el.get_attribute("for")
        ftype = "text"
        options = []
        required = False
        accept = ""

        if for_id:
            field = page.query_selector(f"#{for_id}")
            if field:
                tag = field.evaluate("el => el.tagName.toLowerCase()")
                req = field.get_attribute("required")
                required = req is not None
                if tag == "select":
                    ftype = "select"
                    options = [
                        o.inner_text().strip()
                        for o in page.query_selector_all(f"#{for_id} option")
                        if o.inner_text().strip()
                    ]
                elif tag == "textarea":
                    ftype = "textarea"
                else:
                    ftype = field.get_attribute("type") or "text"
                    if ftype == "file":
                        accept = field.get_attribute("accept") or ""

        add(text, ftype, options, required, accept)

    # Standalone inputs/textareas with aria-label or placeholder
    for sel in ["textarea", "input[aria-label]", "input[placeholder]", "input[type='file']"]:
        for el in root.query_selector_all(sel):
            text = (el.get_attribute("aria-label") or el.get_attribute("placeholder") or "").strip()
            tag = el.evaluate("el => el.tagName.toLowerCase()")
            ftype = "textarea" if tag == "textarea" else (el.get_attribute("type") or "text")
            accept = el.get_attribute("accept") or "" if ftype == "file" else ""
            if ftype == "file" and not text:
                text = "File upload"
            add(text, ftype, accept=accept)

    return questions


def is_application_form(page):
    """
    Return (is_form: bool, reason: str).
    Validates that the current page is a real job application form rather than
    a listing page, search page, or other non-form page.
    """
    url = page.url.lower()

    # Known ATS domains / apply-path signals in the URL
    # Use precise substrings so "/applystart" (Indeed's interstitial) doesn't match
    url_form_signals = any(s in url for s in [
        "/apply/", "/apply?", "/application", "hakemus", "tyohakemus",
        "greenhouse.io", "lever.co", "myworkdayjobs", "smartrecruiters",
        "recruitee.com", "teamtailor", "personio", "bamboohr",
        "jobvite", "icims", "taleo", "successfactors",
        "careers/apply", "jobs/apply",
    ]) or url.rstrip("/").endswith("/apply")

    # Reject known listing / search portals unless they have an explicit apply-path
    if not url_form_signals and any(d in url for d in LISTING_DOMAINS):
        return False, f"URL is a job listing portal, not an application form"

    # Count visible non-trivial inputs (exclude hidden, submit, button, search types)
    try:
        visible_inputs = page.evaluate("""() => {
            const skip = new Set(['hidden','submit','button','reset','image','search']);
            return [...document.querySelectorAll('input, textarea, select')]
                .filter(el => {
                    if (el.tagName === 'INPUT' && skip.has((el.type||'').toLowerCase())) return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }).length;
        }""")
    except Exception:
        visible_inputs = 0

    # Count labels that match known application-field keywords
    try:
        label_texts = [el.inner_text().strip().lower() for el in page.query_selector_all("label")]
        label_hits = sum(1 for lbl in label_texts if any(hint in lbl for hint in FORM_LABEL_HINTS))
    except Exception:
        label_hits = 0

    # Check for a submit button with application-type text (not a search button)
    has_apply_submit = False
    for hint in APPLY_SUBMIT_HINTS:
        try:
            btn = page.query_selector(f'button:has-text("{hint}"), input[type="submit"]')
            if btn and btn.is_visible():
                text = (btn.inner_text() or btn.get_attribute("value") or "").lower()
                if not any(s in text for s in SEARCH_SUBMIT_HINTS):
                    has_apply_submit = True
                    break
        except Exception:
            pass

    if visible_inputs >= 5:
        return True, f"{visible_inputs} visible form fields"
    if visible_inputs >= 2 and label_hits >= 2:
        return True, f"{visible_inputs} fields + {label_hits} application label(s)"
    if visible_inputs >= 2 and has_apply_submit:
        return True, f"{visible_inputs} fields + application submit button"
    if visible_inputs >= 1 and url_form_signals:
        return True, f"{visible_inputs} field(s) + ATS/apply URL"
    if visible_inputs == 0:
        return False, "no visible form inputs (likely a job description or login page)"
    if visible_inputs < 2:
        return False, f"only {visible_inputs} form field(s) — likely a search bar, not an application form"
    return False, "inputs found but no application labels, submit button, or ATS URL"


# ── Apply-link discovery (used when is_application_form returns False) ─────────

def find_apply_link_dom(page, base_url):
    """Scan visible DOM links for one that leads to an application form."""
    # Text signals in priority order (most specific first)
    text_signals = [
        "apply now", "apply here", "apply online", "submit application",
        "jätä hakemus", "hae nyt", "hae paikkaa", "lähetä hakemus",
        "apply", "hae",
    ]
    # ATS and apply-path URL signals
    url_signals = [
        "boards.greenhouse.io", "greenhouse.io/jobs",
        "lever.co", "myworkdayjobs.com", "smartrecruiters.com",
        "recruitee.com", "teamtailor.com", "personio.com", "bamboohr.com",
        "jobvite.com", "icims.com", "taleo.net", "successfactors.com",
        "workable.com", "ashbyhq.com", "breezy.hr",
        "/apply", "/application", "hakemus", "careers/apply", "jobs/apply",
    ]

    try:
        links = page.evaluate("""() =>
            [...document.querySelectorAll('a[href]')]
                .filter(a => { const r = a.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                .map(a => ({ text: a.innerText.trim().toLowerCase(), href: a.href }))
                .filter(l => l.href && !l.href.startsWith('javascript:') && !l.href.startsWith('mailto:'))
        """)
    except Exception:
        return None

    for signal in text_signals:
        for link in links:
            if signal in link["text"]:
                return link["href"]

    for link in links:
        if any(p in link["href"].lower() for p in url_signals):
            return link["href"]

    return None


def find_apply_link_vision(page):
    """Take a viewport screenshot and ask Claude Haiku to identify the apply link."""
    try:
        screenshot_b64 = base64.standard_b64encode(page.screenshot()).decode()

        # Give Claude the raw link list so it can return an exact URL
        try:
            links = page.evaluate("""() =>
                [...document.querySelectorAll('a[href], button')]
                    .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                    .map(el => ({ text: el.innerText.trim(), href: el.tagName === 'A' ? el.href : null }))
                    .filter(l => l.text && l.text.length < 120)
                    .slice(0, 60)
            """)
        except Exception:
            links = []

        links_text = "\n".join(
            f'- "{l["text"]}" -> {l["href"] or "(button, no href)"}'
            for l in links if l.get("text")
        )

        # Prefer ANTHROPIC_API_KEY; fall back to Claude Code's OAuth token
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            client = anthropic.Anthropic(api_key=api_key)
        else:
            creds_path = Path.home() / ".claude" / ".credentials.json"
            if creds_path.exists():
                creds_data = json.loads(creds_path.read_text(encoding="utf-8"))
                access_token = creds_data.get("claudeAiOauth", {}).get("accessToken")
                if not access_token:
                    raise ValueError("No accessToken in Claude credentials")
                client = anthropic.Anthropic(auth_token=access_token)
            else:
                raise ValueError("No ANTHROPIC_API_KEY and no ~/.claude/.credentials.json found")

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a screenshot of a job-related web page. "
                            "I need the URL of the actual job application form.\n\n"
                            f"Visible links and buttons on this page:\n{links_text}\n\n"
                            "Find the link that leads to a form where a candidate fills in their details "
                            "and submits an application. Look for: Apply, Apply now, Hae, Hae nyt, "
                            "Jätä hakemus, or links to ATS systems (Greenhouse, Lever, Workday, etc.).\n\n"
                            "Return ONLY the exact URL from the list above. If none found, return: NOT_FOUND"
                        ),
                    },
                ],
            }],
        )

        result = resp.content[0].text.strip()
        return result if result.startswith("http") else None

    except Exception as e:
        print(f"  Vision analysis error: {e}")
        return None


def find_actual_apply_url(page, base_url):
    """
    When the current page is not a form, try to discover the actual apply URL.
    Tries DOM scan first (free), then Claude Haiku vision as fallback.
    Returns (url: str | None, method: str | None).
    """
    print("  Searching for actual application form link...")

    url = find_apply_link_dom(page, base_url)
    if url:
        url = html_unescape(url)  # fix &amp; → & in href attributes
        print(f"  Found via DOM scan: {url}")
        return url, "DOM scan"

    print("  DOM scan found nothing — trying screenshot analysis...")
    url = find_apply_link_vision(page)
    if url:
        url = html_unescape(url)
        print(f"  Found via vision analysis: {url}")
        return url, "vision"

    return None, None


def _navigate_to_form(page, url):
    """Navigate to a URL, tolerating networkidle timeouts (common on analytics-heavy sites)."""
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
    time.sleep(2)


def fill_required_dummy(page):
    """Fill required empty fields with placeholder values so 'Next' is clickable."""
    for inp in page.query_selector_all("input[required]:not([type='hidden'])"):
        try:
            if not inp.input_value():
                itype = inp.get_attribute("type") or "text"
                if itype in ("text", "search"):
                    inp.fill("N/A")
                elif itype == "email":
                    inp.fill("test@example.com")
                elif itype == "tel":
                    inp.fill("+358000000000")
                elif itype == "number":
                    inp.fill("1")
                elif itype == "checkbox":
                    inp.check()
        except Exception:
            pass
    for ta in page.query_selector_all("textarea[required]"):
        try:
            if not ta.input_value():
                ta.fill("N/A")
        except Exception:
            pass


# ── LinkedIn scraper ───────────────────────────────────────────────────────────

def login_linkedin(page, email, password):
    print("  Logging in to LinkedIn...")
    page.goto("https://www.linkedin.com/login", wait_until="networkidle")
    time.sleep(1)
    page.fill("#username", email)
    page.fill("#password", password)
    page.click('[type="submit"]')
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    if any(k in page.url for k in ("checkpoint", "challenge", "verification")):
        print()
        print("  LinkedIn is asking for verification (email/phone/captcha).")
        print("  Complete it in the browser window, then press Enter here.")
        input("  Press Enter when you have verified and can see your LinkedIn feed: ")


def scrape_linkedin(page, job_url, context, email, password):
    spath = session_file("linkedin")

    page.goto(job_url, wait_until="networkidle")
    time.sleep(2)

    if needs_relogin(page, "linkedin"):
        login_linkedin(page, email, password)
        context.storage_state(path=str(spath))
        print(f"  Session saved → {spath}")
        page.goto(job_url, wait_until="networkidle")
        time.sleep(2)

    # Find Easy Apply button
    easy_apply = None
    for sel in [
        'button[data-control-name="jobdetails_topcard_inapply"]',
        'button.jobs-apply-button',
        'button:has-text("Easy Apply")',
        'button:has-text("Hae helposti")',
    ]:
        try:
            easy_apply = page.query_selector(sel)
            if easy_apply:
                break
        except Exception:
            pass

    if not easy_apply:
        print("  No Easy Apply button found on this listing.")
        return [], page.url

    easy_apply.click()
    time.sleep(2)

    all_questions = []
    step = 0

    while step < 15:
        step += 1
        time.sleep(1.2)
        step_questions = extract_form_fields(page)
        for q in step_questions:
            if q["label"] not in [x["label"] for x in all_questions]:
                q["step"] = step
                all_questions.append(q)

        # Check if we're on the review/submit step
        review_btn = page.query_selector(
            'button[aria-label="Review your application"], button:has-text("Review"), button:has-text("Tarkista")'
        )
        submit_btn = page.query_selector(
            'button[aria-label="Submit application"], button:has-text("Submit application")'
        )
        if submit_btn:
            print(f"  Reached submit step — NOT submitting. Collected {len(all_questions)} questions.")
            break

        next_btn = page.query_selector(
            'button[aria-label="Continue to next step"], button:has-text("Next"), '
            'button:has-text("Seuraava"), button:has-text("Continue")'
        )
        if review_btn:
            review_btn.click()
            time.sleep(1.2)
            continue
        if next_btn:
            fill_required_dummy(page)
            next_btn.click()
            time.sleep(1.5)
        else:
            break

    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    return all_questions, page.url


# ── Indeed scraper ────────────────────────────────────────────────────────────

def indeed_direct_url(url):
    """Convert an Indeed redirect/tracking URL to a stable viewjob URL.
    Redirect URLs expire and don't work when logged in; viewjob?jk= always works."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if "jk" in params:
        base = f"{parsed.scheme}://{parsed.netloc}"
        return f"{base}/viewjob?jk={params['jk'][0]}"
    return url


def login_indeed_google(page, context):
    """Open Indeed login page and let the user authenticate via Google OAuth."""
    print("  Opening Indeed login — please sign in with Google in the browser window.")
    page.goto("https://secure.indeed.com/auth", wait_until="domcontentloaded", timeout=60000)
    time.sleep(2)

    # Click "Continue with Google" if the button is already visible
    for sel in [
        '[data-tn-element="google-auth-button"]',
        'a:has-text("Continue with Google")',
        'button:has-text("Continue with Google")',
        'a[href*="accounts.google.com"]',
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                time.sleep(2)
                break
        except Exception:
            pass

    print()
    print("  ---------------------------------------------------------")
    print("  Sign in with Google in the browser window.")
    print("  When you see the Indeed home/jobs page, come back here.")
    print("  ---------------------------------------------------------")
    input("  Press Enter once you are fully logged into Indeed: ")

    # Wait for any redirect to settle
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    spath = session_file("indeed")
    context.storage_state(path=str(spath))
    print(f"  Session saved → {spath}")


def login_indeed_password(page, context, email, password):
    """Log into Indeed with email + password."""
    print("  Logging in to Indeed...")
    page.goto("https://secure.indeed.com/auth", wait_until="domcontentloaded", timeout=60000)
    time.sleep(1)

    for sel in ['input[name="email"]', '#ifl-InputFormField-3', 'input[type="email"]']:
        try:
            field = page.query_selector(sel)
            if field:
                field.fill(email)
                break
        except Exception:
            pass

    for sel in ['button:has-text("Continue")', 'button[type="submit"]']:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                time.sleep(1.5)
                break
        except Exception:
            pass

    for sel in ['input[name="password"]', '#ifl-InputFormField-5', 'input[type="password"]']:
        try:
            field = page.query_selector(sel)
            if field:
                field.fill(password)
                break
        except Exception:
            pass

    for sel in ['button:has-text("Sign in")', 'button[type="submit"]']:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                time.sleep(2)
                break
        except Exception:
            pass

    if "login" in page.url or "auth" in page.url:
        print()
        print("  Indeed may require verification. Complete it in the browser, then press Enter.")
        input("  Press Enter when you can see your Indeed dashboard: ")

    context.storage_state(path=str(spath := session_file("indeed")))
    print(f"  Session saved → {spath}")


def login_workday(page, email, password):
    """Automate sign-in on a Workday 'Create Account / Sign In' step."""
    print("  Logging in to Workday...")

    # Activate "Sign In" tab/section if present (vs "Create Account")
    for sel in [
        '[data-automation-id="signIn"]',
        'button:has-text("Sign In")',
        'a:has-text("Sign In")',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                time.sleep(1)
                break
        except Exception:
            pass

    # Fill email
    for sel in [
        '[data-automation-id="email"]',
        'input[type="email"]',
        'input[autocomplete="username"]',
        'input[name="email"]',
        'input[placeholder*="email" i]',
    ]:
        try:
            fld = page.query_selector(sel)
            if fld and fld.is_visible():
                fld.fill(email)
                break
        except Exception:
            pass

    # Some Workday portals split email/password into two pages — click Next if needed
    for sel in [
        '[data-automation-id="click_filter"]',
        'button:has-text("Next")',
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                label_text = (btn.inner_text() or "").strip().lower()
                if label_text not in ("sign in", "kirjaudu"):
                    btn.click()
                    time.sleep(1.5)
                    break
        except Exception:
            pass

    # Fill password
    for sel in [
        '[data-automation-id="password"]',
        'input[type="password"]',
        'input[autocomplete="current-password"]',
        'input[name="password"]',
    ]:
        try:
            fld = page.query_selector(sel)
            if fld and fld.is_visible():
                fld.fill(password)
                break
        except Exception:
            pass

    # Submit
    for sel in [
        '[data-automation-id="click_filter"]',
        'button:has-text("Sign In")',
        'button[type="submit"]',
    ]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                time.sleep(3)
                break
        except Exception:
            pass

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    if detect_login_wall(page):
        print()
        print("  Workday login may need verification — please complete it in the browser.")
        input("  Press Enter once you are past the login screen: ")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass


def wait_for_cloudflare(page, target_url, timeout=20):
    """
    Wait up to `timeout` seconds for Cloudflare's challenge to clear automatically.
    If it doesn't, ask the user to complete it in the visible browser window.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        title = ""
        try:
            title = page.evaluate("document.title") or ""
        except Exception:
            pass
        if "just a moment" not in title.lower():
            return
        time.sleep(2)

    # Auto-solve failed — ask user to intervene
    print()
    print("  ---------------------------------------------------------")
    print("  Cloudflare bot challenge detected in the browser.")
    print(f"  Please navigate to this page in the open browser window:")
    print(f"  {target_url}")
    print("  Complete any CAPTCHA if shown, then wait for the job page to load.")
    print("  ---------------------------------------------------------")
    input("  Press Enter once the job page is fully loaded: ")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass


def goto_indeed(page, url):
    """Navigate to an Indeed page using domcontentloaded (networkidle never fires)."""
    direct = indeed_direct_url(url)
    try:
        page.goto(direct, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass
    time.sleep(2)
    wait_for_cloudflare(page, direct)
    return direct


def scrape_indeed(page, job_url, context, email, password, is_new_profile=False, job_id="", non_interactive=False):
    direct_url = goto_indeed(page, job_url)

    # Login needed if: brand-new profile, or Indeed redirected us to a login wall
    if is_new_profile or needs_relogin(page, "indeed"):
        if is_google_auth("indeed"):
            login_indeed_google(page, context)
        else:
            login_indeed_password(page, context, email, password)
        goto_indeed(page, job_url)

    accept_cookies(page)
    time.sleep(1)

    # Detect redirect to homepage (job no longer available)
    current = page.url
    if "viewjob" not in current and "/rc/clk" not in current and "jk=" not in current:
        print(f"\n  EXPIRED: Job page not found — redirected to {current}")
        print("  Reason: The job listing URL has expired or the posting was removed.")
        _scrape_flags["expired"] = True
        return [], direct_url

    # Detect expired/outdated listing (DOM selectors + full-text fallback)
    outdated_selectors = [
        '[data-testid="outdated-job-alert"]',
        ':has-text("Tama tyopaikkailmoitus Indeedissa on vanhentunut")',
    ]
    for sel in outdated_selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                print(f"\n  EXPIRED: Job listing is outdated.")
                _scrape_flags["expired"] = True
                return [], direct_url
        except Exception:
            pass
    if detect_expired_listing(page):
        print(f"\n  EXPIRED: Job listing is no longer active.")
        print(f"  Page: {page.url}")
        _scrape_flags["expired"] = True
        return [], direct_url

    # Scroll down and wait for JS-rendered apply button
    try:
        page.evaluate("window.scrollBy(0, 300)")
    except Exception:
        pass
    time.sleep(1.5)

    # Look for an Apply / Hae button to open the form
    apply_url = direct_url
    apply_found = False
    apply_el = None

    # Strategy 1: Indeed data-testid attributes
    for sel in [
        '[data-testid="job-apply-link"]',
        '[data-testid="indeedApplyButton"]',
        '[data-testid="applyButton"]',
        '[aria-label*="Apply"]', '[aria-label*="Hae"]',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                apply_el = el
                break
        except Exception:
            pass

    # Strategy 2: Playwright has-text selectors
    if not apply_el:
        for sel in [
            'button:has-text("Apply now")', 'button:has-text("Apply")',
            'a:has-text("Apply now")', 'a:has-text("Apply")',
            'a:has-text("Hae työpaikkaa")', 'a:has-text("Hae nyt")', 'a:has-text("Hae")',
            'button:has-text("Hae nyt")', 'button:has-text("Hae")',
        ]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    apply_el = el
                    break
            except Exception:
                pass

    # Strategy 3: JS scan — find any visible <a> whose inner text contains apply keywords
    if not apply_el:
        try:
            debug = page.evaluate("""() => {
                const keywords = ['hae', 'apply'];
                const hits = [];
                let found_href = null;
                for (const a of document.querySelectorAll('a')) {
                    const r = a.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    const txt = a.innerText.toLowerCase().trim();
                    if (txt.length > 0 && keywords.some(k => txt.includes(k))) {
                        hits.push({text: txt.slice(0, 80), href: a.href});
                        if (!found_href && a.href &&
                            !a.href.startsWith('javascript:') && !a.href.startsWith('mailto:') &&
                            !a.href.includes('login') && !a.href.includes('signin')) {
                            found_href = a.href;
                        }
                    }
                }
                return {title: document.title, hits: hits, found: found_href};
            }""")
            print(f"  [debug] page title: {debug.get('title','?')}")
            print(f"  [debug] apply-keyword links ({len(debug.get('hits',[]))}): {debug.get('hits',[])[:5]}")
            result = debug.get("found")
            if result:
                apply_found = True
                apply_url = result
                print(f"  Found apply link via JS scan: {apply_url}")
        except Exception as e:
            print(f"  [debug] JS scan error: {e}")

    if apply_el and not apply_found:
        apply_found = True
        href = apply_el.get_attribute("href")
        if href and not href.startswith("javascript:"):
            apply_url = href if href.startswith("http") else urljoin(direct_url, href)
            print(f"  Navigating to apply URL: {apply_url}")
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
        else:
            pages_before = len(context.pages)
            apply_el.click()
            time.sleep(2)
            if len(context.pages) > pages_before:
                # Button opened a new tab (external apply link)
                page = context.pages[-1]
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
            else:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
            apply_url = page.url
        time.sleep(2)
        accept_cookies(page)
    elif apply_found:
        # URL found directly via JS scan — navigate to it
        try:
            page.goto(apply_url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        time.sleep(2)
        accept_cookies(page)

    if not apply_found:
        print(f"  No Apply button found — trying to discover the application link...")
        found_url, method = find_actual_apply_url(page, direct_url)
        if found_url:
            print(f"  Navigating to discovered URL ({method}): {found_url}")
            _navigate_to_form(page, found_url)
            accept_cookies(page)
            apply_url = found_url
            already, date_str = detect_already_applied(page)
            if already:
                msg = f"Applied {date_str}" if date_str else "Already applied"
                print(f"  {msg} — marking dashboard...")
                mark_job_applied_firestore(job_id, date_str)
                return [], job_url
            is_form, form_reason = is_application_form(page)
            if is_form:
                print(f"  Form confirmed: {form_reason}")
                questions = extract_form_fields(page)
                return questions, apply_url
            if navigate_through_login(page, context, found_url, non_interactive=non_interactive):
                already, date_str = detect_already_applied(page)
                if already:
                    msg = f"Applied {date_str}" if date_str else "Already applied"
                    print(f"  {msg} — marking dashboard...")
                    mark_job_applied_firestore(job_id, date_str)
                    return [], job_url
                # User may have navigated to the form themselves — check current page first
                is_form, form_reason = is_application_form(page)
                if not is_form:
                    _navigate_to_form(page, found_url)
                    accept_cookies(page)
                    is_form, form_reason = is_application_form(page)
                if is_form:
                    print(f"  Form confirmed after login: {form_reason}")
                    questions = extract_form_fields(page)
                    return questions, page.url
            print(f"\n  SKIP: Discovered URL is not a form: {form_reason}")
            print(f"  URL: {page.url}")
        else:
            print(f"\n  SKIP: No Apply button found and no form link discovered.")
            print("  Reason: The job may be closed, or the application is hosted externally.")
            print(f"  Page: {page.url}")
        return [], direct_url

    # Validate the apply destination is actually a form; if not, try to find the real one
    is_form, form_reason = is_application_form(page)
    if not is_form:
        print(f"  Not a form: {form_reason} (URL: {page.url})")
        already, date_str = detect_already_applied(page)
        if already:
            msg = f"Applied {date_str}" if date_str else "Already applied"
            print(f"  {msg} — marking dashboard...")
            mark_job_applied_firestore(job_id, date_str)
            return [], job_url
        if navigate_through_login(page, context, apply_url, non_interactive=non_interactive):
            already, date_str = detect_already_applied(page)
            if already:
                msg = f"Applied {date_str}" if date_str else "Already applied"
                print(f"  {msg} — marking dashboard...")
                mark_job_applied_firestore(job_id, date_str)
                return [], job_url
            is_form, form_reason = is_application_form(page)
            if not is_form:
                _navigate_to_form(page, apply_url)
                accept_cookies(page)
                is_form, form_reason = is_application_form(page)
            if is_form:
                print(f"  Form confirmed after login: {form_reason}")
                questions = extract_form_fields(page)
                return questions, page.url
        if not is_form:
            found_url, method = find_actual_apply_url(page, apply_url)
            if found_url:
                print(f"  Navigating to form ({method}): {found_url}")
                _navigate_to_form(page, found_url)
                accept_cookies(page)
                apply_url = page.url
                is_form, form_reason = is_application_form(page)
                if not is_form:
                    if navigate_through_login(page, context, found_url, non_interactive=non_interactive):
                        already, date_str = detect_already_applied(page)
                        if already:
                            msg = f"Applied {date_str}" if date_str else "Already applied"
                            print(f"  {msg} — marking dashboard...")
                            mark_job_applied_firestore(job_id, date_str)
                            return [], job_url
                        is_form, form_reason = is_application_form(page)
                        if not is_form:
                            _navigate_to_form(page, found_url)
                            accept_cookies(page)
                            is_form, form_reason = is_application_form(page)
                if not is_form:
                    print(f"\n  SKIP: Still not a form after navigation.")
                    print(f"  Reason: {form_reason}")
                    print(f"  URL: {page.url}")
                    return [], apply_url
            else:
                print(f"\n  SKIP: Apply button found but could not reach an application form.")
                print(f"  URL: {page.url}")
                return [], apply_url
    print(f"  Form confirmed: {form_reason}")

    questions = extract_form_fields(page)
    return questions, apply_url


# ── Generic / Duunitori / Greenhouse / Lever scraper ─────────────────────────

def accept_cookies(page):
    """Click common cookie-accept buttons (Finnish + English). Best-effort."""
    selectors = [
        'button:has-text("Hyväksy")', 'button:has-text("Hyväksy kaikki")',
        'button:has-text("Hyväksy evästeet")', 'button:has-text("Salli kaikki")',
        'button:has-text("Hyväksyn")',
        'button:has-text("Accept all")', 'button:has-text("Accept All")',
        'button:has-text("Accept cookies")', 'button:has-text("Allow all")',
        'button:has-text("I agree")', 'button:has-text("OK")',
        '#onetrust-accept-btn-handler', '.cc-accept', '.cc-btn.cc-allow',
        '[data-cookiebanner="accept_button"]', '[aria-label="Accept cookies"]',
    ]
    for sel in selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                time.sleep(0.8)
                return True
        except Exception:
            pass
    return False


def dismiss_cookie_overlays(page):
    """After accepting cookies, wait for the overlay to actually leave the DOM."""
    for sel in [
        '[class*="cookie-banner"]', '[id*="cookie-banner"]',
        '[class*="cookieconsent"]', '[id*="cookieconsent"]',
        '[class*="cookie-overlay"]', '[class*="consent-banner"]',
        '[class*="cookie-wall"]',   '[id*="cookie-wall"]',
        '[class*="cookie-notice"]', '[id*="cookie-notice"]',
    ]:
        try:
            page.wait_for_selector(sel, state="hidden", timeout=2000)
            break
        except Exception:
            pass


def scrape_generic(page, job_url, context=None, job_id="", non_interactive=False):
    page.goto(job_url, wait_until="domcontentloaded")
    time.sleep(2)

    accepted = accept_cookies(page)
    if accepted:
        time.sleep(1)
        dismiss_cookie_overlays(page)
        time.sleep(0.5)
    else:
        time.sleep(1)

    if detect_expired_listing(page):
        print(f"\n  EXPIRED: Job listing is no longer active.")
        print(f"  Page: {page.url}")
        _scrape_flags["expired"] = True
        return [], job_url

    apply_url = job_url

    # Try to find and follow an Apply link
    for sel in [
        'a:has-text("Apply now")', 'a:has-text("Apply")',
        'a:has-text("Hae nyt")', 'a:has-text("Hae paikkaa")', 'a:has-text("Hae")',
        'a[href*="apply"]', 'a[href*="application"]',
        'button:has-text("Apply")', 'button:has-text("Hae")',
    ]:
        try:
            el = page.query_selector(sel)
            if el:
                href = el.get_attribute("href")
                if href:
                    apply_url = href if href.startswith("http") else urljoin(job_url, href)
                    page.goto(apply_url, wait_until="domcontentloaded")
                    time.sleep(2)
                    accepted = accept_cookies(page)
                    if accepted:
                        time.sleep(1)
                        dismiss_cookie_overlays(page)
                else:
                    el.click()
                    page.wait_for_load_state("domcontentloaded")
                    apply_url = page.url
                    time.sleep(2)
                break
        except Exception:
            pass

    # Validate the page is actually an application form; if not, try to find the real one
    is_form, form_reason = is_application_form(page)
    if not is_form:
        print(f"  Not a form: {form_reason} (URL: {page.url})")
        already, date_str = detect_already_applied(page)
        if already:
            msg = f"Applied {date_str}" if date_str else "Already applied"
            print(f"  {msg} — marking dashboard...")
            mark_job_applied_firestore(job_id, date_str)
            return [], job_url
        if context and navigate_through_login(page, context, apply_url, non_interactive=non_interactive):
            already, date_str = detect_already_applied(page)
            if already:
                msg = f"Applied {date_str}" if date_str else "Already applied"
                print(f"  {msg} — marking dashboard...")
                mark_job_applied_firestore(job_id, date_str)
                return [], job_url
            # Check where user ended up first, then fall back to apply_url
            is_form, form_reason = is_application_form(page)
            if not is_form:
                _navigate_to_form(page, apply_url)
                accept_cookies(page)
                is_form, form_reason = is_application_form(page)
            if is_form:
                print(f"  Form confirmed after login: {form_reason}")
        if not is_form:
            found_url, method = find_actual_apply_url(page, apply_url)
            if found_url:
                print(f"  Navigating to form ({method}): {found_url}")
                _navigate_to_form(page, found_url)
                accept_cookies(page)
                apply_url = page.url
                already, date_str = detect_already_applied(page)
                if already:
                    msg = f"Applied {date_str}" if date_str else "Already applied"
                    print(f"  {msg} — marking dashboard...")
                    mark_job_applied_firestore(job_id, date_str)
                    return [], job_url
                is_form, form_reason = is_application_form(page)
                if not is_form and context:
                    if navigate_through_login(page, context, found_url, non_interactive=non_interactive):
                        already, date_str = detect_already_applied(page)
                        if already:
                            msg = f"Applied {date_str}" if date_str else "Already applied"
                            print(f"  {msg} — marking dashboard...")
                            mark_job_applied_firestore(job_id, date_str)
                            return [], job_url
                        is_form, form_reason = is_application_form(page)
                        if not is_form:
                            _navigate_to_form(page, found_url)
                            accept_cookies(page)
                            is_form, form_reason = is_application_form(page)
                if not is_form:
                    print(f"\n  SKIP: Still not a form after navigation.")
                    print(f"  Reason: {form_reason}")
                    print(f"  URL: {page.url}")
                    return [], apply_url
            else:
                print(f"\n  SKIP: Could not find an application form link on this page.")
                print(f"  URL: {page.url}")
                return [], apply_url
    print(f"  Form confirmed: {form_reason}")

    # Scope extraction to the URL fragment section if present (e.g. #tyohakemus)
    # This prevents picking up navigation labels, cookie widgets, etc.
    fragment = apply_url.split("#")[-1] if "#" in apply_url else None
    scope = None
    if fragment:
        scope = page.query_selector(f"#{fragment}")
        if scope:
            print(f"  Scoping extraction to #{fragment}")
        else:
            print(f"  Fragment #{fragment} not found — scanning full page")

    questions = extract_form_fields(page, scope=scope)

    # Fallback: full page if scoped extraction found nothing
    if not questions and scope:
        print("  Scoped extraction empty — falling back to full page")
        questions = extract_form_fields(page)

    return questions, apply_url


# ── Workday scraper ───────────────────────────────────────────────────────────

def scrape_workday(page, job_url, context, email, password):
    """
    Scrape a Workday multi-step application form.

    Walks through form steps automatically when required fields are already
    filled (pre-filled from the Workday account).  If a step has unfilled
    required fields, pauses and asks the user to fill them manually and click
    Next in the browser — avoids saving dummy data to the real application.
    Stops before the Review/Submit step (never submits).
    """
    page.goto(job_url, wait_until="networkidle", timeout=45000)
    time.sleep(2)
    accept_cookies(page)

    # Workday renders fields via heavy JS — wait up to 6 s for any input to appear
    try:
        page.wait_for_selector("input, select, textarea", timeout=6000)
    except Exception:
        pass
    time.sleep(1)

    # Also treat Workday "Create Account/Sign In" step text as a login wall signal
    def _is_workday_login_page(pg):
        if detect_login_wall(pg):
            return True
        try:
            body = pg.evaluate("() => document.body.innerText").lower()
            return "create account" in body and "sign in" in body
        except Exception:
            return False

    if _is_workday_login_page(page):
        print("  Login wall detected — attempting Workday sign-in...")
        login_workday(page, email, password)
        time.sleep(3)
        if _is_workday_login_page(page):
            print("  Workday login failed — no questions extracted.")
            return [], job_url
        # After login Workday sometimes redirects to the careers homepage
        if "apply" not in page.url.lower():
            page.goto(job_url, wait_until="networkidle", timeout=45000)
            time.sleep(2)
            accept_cookies(page)

    all_questions = []
    seen_labels: set = set()
    step = 0
    max_steps = 8

    while step < max_steps:
        step += 1
        time.sleep(1.5)
        accept_cookies(page)

        # Stop at Review/Submit — never submit
        try:
            body = page.evaluate("() => document.body.innerText").lower()
        except Exception:
            body = ""
        if any(s in body for s in [
            "review and submit", "review your application", "tarkista ja lähetä",
        ]):
            print(f"  Step {step}: reached Review — stopping (NOT submitting).")
            break

        # Extract all visible fields on this step
        step_fields = extract_form_fields(page)
        new_fields = [q for q in step_fields if q["label"] not in seen_labels]
        for q in new_fields:
            seen_labels.add(q["label"])
            q["step"] = step
            all_questions.append(q)
        print(f"  Step {step}: {len(new_fields)} new field(s) ({len(all_questions)} total)")

        # Locate the Next button
        next_btn = None
        for sel in [
            '[data-automation-id="bottom-navigation-next-button"]',
            '[data-automation-id="nextButton"]',
            'button:has-text("Next")',
            'button:has-text("Save and Continue")',
            'button:has-text("Seuraava")',
        ]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    next_btn = btn
                    break
            except Exception:
                pass

        if not next_btn:
            print(f"  No Next button on step {step} — done.")
            break

        # Count unfilled required text/select fields (skip checkboxes, radios, files)
        try:
            unfilled = page.evaluate("""() => {
                const skip = new Set([
                    'hidden','submit','button','reset','image',
                    'search','file','checkbox','radio'
                ]);
                return [...document.querySelectorAll('[required]')]
                    .filter(el => {
                        if (el.tagName === 'INPUT' &&
                                skip.has((el.type || '').toLowerCase())) return false;
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) return false;
                        return !(el.value || '').trim();
                    }).length;
            }""")
        except Exception:
            unfilled = 0

        if unfilled > 0:
            # Don't fill dummy data into a real Workday application — pause instead
            print(f"  Step {step} has {unfilled} unfilled required field(s).")
            print("  Please fill them in the browser and click Next yourself,")
            print("  then press Enter here to continue. Type 'stop' to stop scraping.")
            choice = input("  [Enter / stop]: ").strip().lower()
            if choice == "stop":
                break
            # User advanced the page manually — re-enter the loop to extract + advance
            continue

        next_btn.click()
        time.sleep(2)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

    return all_questions, page.url


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global PRIVATE_DIR, ENV_FILE, SESSION_DIR

    parser = argparse.ArgumentParser(description="Scrape job application form questions.")
    parser.add_argument("--job-url",     required=True, help="Job listing URL")
    parser.add_argument("--job-id",      required=True, help="Job ID")
    parser.add_argument("--out-dir",     required=True, help="Output directory")
    parser.add_argument("--private-dir", default=str(PRIVATE_DIR),
                        help="Path to private repo (for .env and sessions)")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Never block on input() (unattended runs, e.g. /fill-form auto). "
                             "A login wall that a saved session can't clear is recorded via "
                             "the login_wall flag in the output JSON instead of prompting.")
    args = parser.parse_args()

    PRIVATE_DIR = Path(args.private_dir)
    ENV_FILE    = PRIVATE_DIR / ".env"
    SESSION_DIR = PRIVATE_DIR / "sessions"

    load_env_file()

    platform = detect_platform(args.job_url)
    print(f"Detected platform: {platform}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.job_id}_questions.json"

    spath = session_file(platform)
    has_session = spath.exists()

    # Browser is visible if this is a first-time login (user may need to verify)
    headless = has_session

    email, password = ensure_credentials(platform)

    questions = []
    apply_url = args.job_url
    _scrape_flags["expired"] = False
    _scrape_flags["login_wall"] = False

    with sync_playwright() as p:
        if platform == "indeed":
            # Google OAuth blocks Playwright's automation flags even in real Chrome.
            # A persistent profile sidesteps this: Chrome accumulates real cookies/history,
            # Google sees it as a genuine browser, and OAuth works without warnings.
            profile_dir = PRIVATE_DIR / "chrome_profile_indeed"
            profile_dir.mkdir(parents=True, exist_ok=True)
            is_new_profile = not any(profile_dir.iterdir())

            ctx_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions-except=",
            ]
            ctx_kwargs = dict(
                user_data_dir=str(profile_dir),
                headless=False,
                args=ctx_args,
            )
            for channel in ("chrome", "msedge"):
                try:
                    context = p.chromium.launch_persistent_context(channel=channel, **ctx_kwargs)
                    print(f"  Using persistent {channel} profile: {profile_dir}")
                    break
                except Exception:
                    pass
            else:
                print("  Warning: Chrome/Edge not found — falling back to Chromium")
                context = p.chromium.launch_persistent_context(**ctx_kwargs)

            page = context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            try:
                questions, apply_url = scrape_indeed(
                    page, args.job_url, context, email, password, is_new_profile,
                    job_id=args.job_id, non_interactive=args.non_interactive,
                )
            except Exception as e:
                print(f"  ERROR: {e}", file=sys.stderr)
            finally:
                context.close()

        elif platform == "workday":
            profile_dir = PRIVATE_DIR / "chrome_profile_workday"
            profile_dir.mkdir(parents=True, exist_ok=True)

            ctx_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions-except=",
            ]
            ctx_kwargs = dict(
                user_data_dir=str(profile_dir),
                headless=False,
                args=ctx_args,
            )
            for channel in ("chrome", "msedge"):
                try:
                    context = p.chromium.launch_persistent_context(channel=channel, **ctx_kwargs)
                    print(f"  Using persistent {channel} profile: {profile_dir}")
                    break
                except Exception:
                    pass
            else:
                print("  Warning: Chrome/Edge not found — falling back to Chromium")
                context = p.chromium.launch_persistent_context(**ctx_kwargs)

            page = context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            try:
                questions, apply_url = scrape_workday(
                    page, args.job_url, context, email, password
                )
            except Exception as e:
                print(f"  ERROR: {e}", file=sys.stderr)
            finally:
                context.close()

        else:
            import socket
            def is_port_in_use(port):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    return s.connect_ex(('127.0.0.1', port)) == 0

            if is_port_in_use(9222):
                print("  Automation Profile locked (port 9222 in use). Connecting over CDP...")
                browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
                context = browser.contexts[0]
                is_cdp = True
            else:
                user_data_dir = os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Users\vinee\AppData\Local"), r"Google\Chrome\Automation Profile")
                context = p.chromium.launch_persistent_context(
                    user_data_dir,
                    channel="chrome",
                    headless=headless,
                    viewport={"width": 1280, "height": 900}
                )
                is_cdp = False
            page = context.pages[0] if context.pages else context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            try:
                if platform == "linkedin":
                    questions, apply_url = scrape_linkedin(page, args.job_url, context, email, password)
                    if not has_session:
                        context.storage_state(path=str(spath))
                        print(f"  Session saved → {spath}")
                else:
                    questions, apply_url = scrape_generic(
                        page, args.job_url, context, job_id=args.job_id,
                        non_interactive=args.non_interactive,
                    )

            except Exception as e:
                print(f"  ERROR: {e}", file=sys.stderr)
            finally:
                if is_cdp:
                    # Browser.close() on a connect_over_cdp() browser only detaches the
                    # client -- it does not terminate the actual remote Chrome process
                    # (unlike a launched browser). Browser has no disconnect() method.
                    browser.close()
                else:
                    context.close()

    is_expired = _scrape_flags.get("expired", False)
    is_login_wall = _scrape_flags.get("login_wall", False)

    result = {
        "job_id":         args.job_id,
        "job_url":        args.job_url,
        "apply_url":      apply_url,
        "platform":       platform,
        "expired":        is_expired,
        "login_wall":     is_login_wall,
        "question_count": len(questions),
        "questions":      questions,
    }

    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    if is_expired:
        print(f"\nEXPIRED: Job listing is no longer active — no questions extracted.")
        print(f"  Moving job {args.job_id} to deleted.json (unless already applied)...")
        move_job_to_deleted(args.job_id, "Expired job listing (detected by scraper)")
        sys.exit(0)

    print(f"\nSaved {len(questions)} questions -> {out_path}")

    if questions:
        print("\nQuestions found:")
        for i, q in enumerate(questions, 1):
            opts = f" [{', '.join(q['options'][:3])}{'...' if len(q['options']) > 3 else ''}]" if q.get("options") else ""
            step = f" (step {q['step']})" if "step" in q else ""
            print(f"  {i}. [{q['type']}] {q['label']}{opts}{step}")
    else:
        print("  No questions extracted (login wall, no Easy Apply, or unsupported ATS).")
        sys.exit(1)


if __name__ == "__main__":
    main()
