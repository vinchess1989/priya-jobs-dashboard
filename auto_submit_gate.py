"""Decide whether /fill-form may submit an application on its own, and record the outcome.

Priya's rule (2026-10-01): starred (favorite) jobs are filled and left for her to review and
submit; every other job may be submitted automatically - but only when ALL the checks below
pass. Anything that fails a check falls back to the review path, so the safe default is always
"don't submit".

    python auto_submit_gate.py check  --job-id ID --apply-url URL [--questions Q.json] [--answers A.json]
        -> prints one JSON line: {"decision": "auto"|"review", "reasons": [...], ...}
    python auto_submit_gate.py record --job-id ID --result RESULT.json
        -> writes the outcome to Firestore (applied / form_filled / action_item)

answers.json format (written by /fill-form Step 3):
    {"apply_url": "...", "answers": [{"label": "...", "value": "...", "placeholder": false}, ...]}
RESULT.json format (written by the fill script, Step 5):
    {"submitted": bool, "confirmed": bool, "blocked_reason": "...", "confirmation_text": "...",
     "final_url": "...", "screenshots": ["...png", ...]}
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

import firestore_auth
import job_status_store as store

BASE_DIR = Path(__file__).resolve().parent
JOBS_FILE = BASE_DIR / "jobs.json"
DELETED_FILE = BASE_DIR / "deleted.json"

# The other Priya board - an application there counts as a duplicate here too.
SIBLING_JOBS_LOCAL = BASE_DIR.parent / "priya_global_jobs" / "jobs.json"
SIBLING_JOBS_URL = "https://vinchess1989.github.io/priya-global-jobs-dashboard/jobs.json"

DAILY_CAP = 10                      # automatic submits per board per local day
COMPANY_COOLDOWN_DAYS = 7           # at most one automatic submit per company per week
SETTINGS_URL = f"{store.FIRESTORE_BASE}/shared_state/settings"

# LinkedIn: automating applications risks the account (manju_jobs memory.md).
# Teamtailor/Biisoni: submit on file upload by themselves (manju_jobs memory.md incident).
NEVER_AUTO_HOSTS = ("linkedin.com", "teamtailor.com", "biisoni.fi")

SALARY_RE = re.compile(r"salary|compensation|remuneration|pay expectation|expected pay|wage|ctc\b|"
                       r"palkka|palkkatoive|palkkatoivomus|desired pay|rate expectation", re.I)
DOB_RE = re.compile(r"date of birth|birth ?date|\bdob\b|syntym", re.I)
OUR_FILES_RE = re.compile(r"resume|cv\b|curriculum|cover letter|motivation letter|ansioluettelo|hakemus", re.I)


def _now():
    return datetime.now().astimezone()


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _norm_company(name):
    s = re.sub(r"\b(oy|oyj|ab|ltd|limited|inc|llc|plc|gmbh|pvt|pty|co|corp|corporation)\b\.?", "", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _all_jobs():
    jobs = (_load(JOBS_FILE) or []) + (_load(DELETED_FILE) or [])
    return {j["url"]: j for j in jobs if j.get("url")}


def _sibling_jobs():
    data = _load(SIBLING_JOBS_LOCAL)
    if data is None:
        try:
            data = requests.get(SIBLING_JOBS_URL, timeout=20).json()
        except Exception:
            data = []
    return data or []


def _find_job(job_id):
    for job in _all_jobs().values():
        if job.get("id") == job_id:
            return job
    return None


def _settings():
    resp = firestore_auth.session().get(SETTINGS_URL, timeout=20)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return store._deserialize_doc(resp.json())


def _entry(status, url):
    """A job's job_status entry; {} for missing or non-job fields (e.g. the doc's "initialized" placeholder)."""
    e = status.get(url)
    return e if isinstance(e, dict) else {}


def _auto_submits(status):
    """[(url, submitted_at datetime)] for every automatic submit recorded on this board."""
    out = []
    for url, entry in status.items():
        if not isinstance(entry, dict):
            continue
        ff = entry.get("form_filled") or {}
        if isinstance(ff, dict) and ff.get("submitted_by") == "fill-form-auto" and ff.get("submitted_at"):
            try:
                out.append((url, datetime.fromisoformat(ff["submitted_at"].replace("Z", "+00:00"))))
            except ValueError:
                pass
    return out


def check(job_id, apply_url, questions_path=None, answers_path=None):
    reasons = []
    job = _find_job(job_id)
    if not job:
        return {"decision": "review", "reasons": [f"job {job_id} not found in jobs.json/deleted.json"]}
    url = job["url"]
    status = store.get_job_status()
    entry = _entry(status, url)

    # 1. Kill switch on the dashboard (off unless explicitly turned on).
    if _settings().get("auto_submit_enabled") is not True:
        reasons.append("auto-submit switch is off on the dashboard")
    # 2. Starred jobs are always Priya's to submit.
    if entry.get("favorite") is True:
        reasons.append("starred as favourite - Priya reviews and submits")
    # 3. Strong matches only.
    match = entry.get("matches_requirements") or job.get("matches_requirements")
    if match != "yes":
        reasons.append(f"match is '{match}', auto-submit is for 'yes' only")
    if (entry.get("applied") or job.get("applied")) == "yes":
        reasons.append("already marked applied")
    # 4/5. Sites that must never be automated.
    host = urlparse(apply_url or "").netloc.lower()
    bad = [h for h in NEVER_AUTO_HOSTS if host == h or host.endswith("." + h)]
    if bad:
        reasons.append(f"apply site {host} is never auto-submitted ({bad[0]})")
    # 6. Daily cap.
    today = _now().date()
    subs = _auto_submits(status)
    today_count = sum(1 for _, t in subs if t.astimezone().date() == today)
    if today_count >= DAILY_CAP:
        reasons.append(f"daily cap reached ({today_count}/{DAILY_CAP} automatic submits today)")
    # 7. Duplicates: same company+title applied on either board; same company auto-submitted this week.
    all_jobs = _all_jobs()
    company, title = _norm_company(job.get("company")), _norm_title(job.get("title"))
    if company:
        for other in list(all_jobs.values()) + _sibling_jobs():
            if other.get("url") == url:
                continue
            other_applied = other.get("applied") == "yes" or _entry(status, other.get("url")).get("applied") == "yes"
            if other_applied and _norm_company(other.get("company")) == company and _norm_title(other.get("title")) == title:
                reasons.append(f"already applied to the same role at {job.get('company')} ({other.get('url')})")
                break
        cutoff = _now() - timedelta(days=COMPANY_COOLDOWN_DAYS)
        for sub_url, t in subs:
            if sub_url != url and t >= cutoff and _norm_company(all_jobs.get(sub_url, {}).get("company")) == company:
                reasons.append(f"another job at {job.get('company')} was auto-submitted in the last {COMPANY_COOLDOWN_DAYS} days")
                break
    # 8. Form content: every required field must have a real answer; salary and DOB go to review.
    questions = (_load(questions_path) or {}).get("questions", []) if questions_path else []
    answers_doc = _load(answers_path) if answers_path else None
    if not answers_doc or not isinstance(answers_doc.get("answers"), list):
        reasons.append("no drafted answers file to verify the form against")
        answers = {}
    else:
        answers = {a.get("label", "").strip(): a for a in answers_doc["answers"]}
    for q in questions:
        label = (q.get("label") or "").strip()
        if SALARY_RE.search(label):
            reasons.append(f"salary question: '{label[:80]}'")
            continue
        if DOB_RE.search(label) and q.get("required"):
            reasons.append(f"date of birth required: '{label[:80]}'")
            continue
        if not q.get("required"):
            continue
        if q.get("type") == "file":
            if not OUR_FILES_RE.search(label):
                reasons.append(f"required upload we don't have: '{label[:80]}'")
            continue
        a = answers.get(label)
        if not a or a.get("placeholder") or not str(a.get("value", "")).strip():
            reasons.append(f"required field without a real answer: '{label[:80]}'")
    for a in answers.values():
        if a.get("placeholder") and SALARY_RE.search(a.get("label", "")):
            reasons.append("salary answer left as placeholder")

    return {"decision": "review" if reasons else "auto", "reasons": reasons, "job_url": url,
            "auto_submits_today": today_count, "daily_cap": DAILY_CAP}


def record(job_id, result_path):
    job = _find_job(job_id)
    if not job:
        sys.exit(f"job {job_id} not found")
    url = job["url"]
    res = _load(result_path) or {}
    now = _now()
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ff = {"filled_at": stamp, "auto_submitted": bool(res.get("submitted")), "submitted_by": "fill-form-auto",
          "confirmation_text": (res.get("confirmation_text") or "")[:300], "final_url": res.get("final_url") or "",
          "screenshots": res.get("screenshots") or []}
    if res.get("submitted") and res.get("confirmed"):
        ff.update(status="submitted", submitted_at=stamp, done_at=stamp)
        store.update_job_fields(url, {"form_filled": ff, "applied": "yes", "applied_date": now.strftime("%Y-%m-%d")})
        # Same channel as the dashboard's Applied toggle, so the scraper syncs it into jobs.json.
        firestore_auth.session().post(f"{store.FIRESTORE_BASE}/user_feedback", json={"fields": {
            "url": {"stringValue": url}, "type": {"stringValue": "applied_update"},
            "applied": {"stringValue": "yes"}, "status": {"stringValue": "unread"},
            "timestamp": {"timestampValue": stamp}}}, timeout=20).raise_for_status()
        outcome = "submitted and confirmed - marked applied"
    elif res.get("submitted"):
        ff.update(status="verify_submission", submitted_at=stamp, done_at=None)
        store.update_job_fields(url, {"form_filled": ff, "action_item": {
            "type": "verify_submission", "status": "pending", "created_at": stamp, "done_at": None,
            "detail": "Submit was clicked automatically but no confirmation page was detected - check "
                      "your email / the site, then mark it applied (or re-apply)."}})
        outcome = "submit clicked but NOT confirmed - action item created, not marked applied"
    else:
        ff.update(status="pending_review", done_at=None, auto_submitted=False,
                  review_reason=res.get("blocked_reason") or "")
        store.update_job_fields(url, {"form_filled": ff})
        outcome = f"not submitted ({res.get('blocked_reason') or 'blocked'}) - left for Priya's review"
    print(json.dumps({"job_id": job_id, "outcome": outcome}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--job-id", required=True)
    c.add_argument("--apply-url", required=True)
    c.add_argument("--questions")
    c.add_argument("--answers")
    r = sub.add_parser("record")
    r.add_argument("--job-id", required=True)
    r.add_argument("--result", required=True)
    a = ap.parse_args()
    if a.cmd == "check":
        print(json.dumps(check(a.job_id, a.apply_url, a.questions, a.answers)))
    else:
        record(a.job_id, a.result)


if __name__ == "__main__":
    main()
