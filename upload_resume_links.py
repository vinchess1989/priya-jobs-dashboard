#!/usr/bin/env python3
"""
Upload resume and cover letter URLs to Firestore for the Priya jobs dashboard.

Usage:
    python upload_resume_links.py                    # uses input.csv in same folder
    python upload_resume_links.py --input links.csv  # custom input file

Input CSV format (columns: job_id, resume_url, cover_letter_url):
    f6aaa66f, https://github.com/vinchess1989/priya-jobs-private/blob/main/Resumes/f6aaa66f_resume.md, https://github.com/vinchess1989/priya-jobs-private/blob/main/Resumes/f6aaa66f_cover_letter.md

Lines starting with # are treated as comments and ignored.
"""

import csv
import json
import os
import subprocess
import sys
import argparse
import requests
from pathlib import Path

PROJECT_ID = "priya-jobs-dashboard"

def _find_resumes_dir() -> str:
    script = Path(__file__).parent / "find_repos.py"
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode == 0:
        data = json.loads(result.stdout)
        private = data.get("private")
        if private:
            return str(Path(private) / "Resumes")
    # Last-resort fallback if find_repos.py can't locate the private repo at all --
    # assumes the conventional sibling-directory layout next to this script's own repo.
    return str(Path(__file__).parent.parent / "Priya_jobs_private" / "Resumes")

RESUMES_DIR = _find_resumes_dir()
FIRESTORE_BASE = (
    f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}"
    f"/databases/(default)/documents"
)
JOBS_JSON_URL = (
    "https://raw.githubusercontent.com/vinchess1989/priya-jobs-dashboard/main/jobs.json"
)


def fetch_jobs_json():
    resp = requests.get(JOBS_JSON_URL, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_id_to_url(jobs):
    return {j["id"]: j["url"] for j in jobs if "id" in j and "url" in j}


def _deserialize_value(v):
    if "stringValue" in v:
        return v["stringValue"]
    if "booleanValue" in v:
        return v["booleanValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "nullValue" in v:
        return None
    if "mapValue" in v:
        return {k: _deserialize_value(fv) for k, fv in v["mapValue"].get("fields", {}).items()}
    if "arrayValue" in v:
        return [_deserialize_value(av) for av in v["arrayValue"].get("values", [])]
    return None


def _deserialize_doc(doc):
    return {k: _deserialize_value(v) for k, v in doc.get("fields", {}).items()}


def _serialize_value(val):
    if val is None:
        return {"nullValue": None}
    if isinstance(val, bool):
        return {"booleanValue": val}
    if isinstance(val, int):
        return {"integerValue": str(val)}
    if isinstance(val, float):
        return {"doubleValue": val}
    if isinstance(val, str):
        return {"stringValue": val}
    if isinstance(val, dict):
        return {"mapValue": {"fields": {k: _serialize_value(v) for k, v in val.items()}}}
    if isinstance(val, list):
        return {"arrayValue": {"values": [_serialize_value(v) for v in val]}}
    return {"stringValue": str(val)}


def _serialize_doc(data):
    return {"fields": {k: _serialize_value(v) for k, v in data.items()}}


def get_job_status():
    url = f"{FIRESTORE_BASE}/shared_state/job_status"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return _deserialize_doc(resp.json())


def patch_job_status(data):
    url = f"{FIRESTORE_BASE}/shared_state/job_status"
    resp = requests.patch(url, json=_serialize_doc(data), timeout=30)
    resp.raise_for_status()


def main():
    parser = argparse.ArgumentParser(
        description="Upload resume/cover letter links to Firestore."
    )
    parser.add_argument(
        "--input", default="input.csv",
        help="CSV file with columns: job_id, resume_url, cover_letter_url"
    )
    args = parser.parse_args()

    print("Fetching jobs.json from GitHub...")
    try:
        jobs = fetch_jobs_json()
    except Exception as e:
        print(f"ERROR: Could not fetch jobs.json: {e}")
        sys.exit(1)

    id_to_url = build_id_to_url(jobs)
    print(f"  Loaded {len(id_to_url)} job IDs.")

    try:
        with open(args.input, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = [row for row in reader if row and not row[0].strip().startswith("#")]
    except FileNotFoundError:
        print(f"ERROR: '{args.input}' not found.")
        print("Create a CSV with columns: job_id, resume_url, cover_letter_url")
        sys.exit(1)

    if not rows:
        print("No data rows in input CSV.")
        sys.exit(0)

    updates = []
    for row in rows:
        job_id = row[0].strip()
        resume_url = row[1].strip() if len(row) > 1 else ""
        cover_letter_url = row[2].strip() if len(row) > 2 else ""

        if job_id not in id_to_url:
            print(f"WARN: job_id '{job_id}' not found in jobs.json — skipping.")
            continue

        tailor_model = ""
        tailored_at = ""
        data_path = os.path.join(RESUMES_DIR, job_id, f"{job_id}_data.json")
        if os.path.exists(data_path):
            try:
                with open(data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tailor_model = data.get("tailor_model", "")
                tailored_at = data.get("tailored_at", "")
            except Exception:
                pass

        updates.append({
            "job_id": job_id,
            "job_url": id_to_url[job_id],
            "resume_url": resume_url,
            "cover_letter_url": cover_letter_url,
            "tailor_model": tailor_model,
            "tailored_at": tailored_at,
        })

    if not updates:
        print("No valid job IDs to update.")
        sys.exit(0)

    print(f"\nFetching current Firestore document...")
    try:
        current = get_job_status()
    except Exception as e:
        print(f"ERROR: Could not read Firestore: {e}")
        sys.exit(1)

    for u in updates:
        entry = current.get(u["job_url"], {})
        if u["resume_url"]:
            entry["resume_url"] = u["resume_url"]
        if u["cover_letter_url"]:
            entry["cover_letter_url"] = u["cover_letter_url"]
        if u["tailor_model"]:
            entry["tailor_model"] = u["tailor_model"]
        if u["tailored_at"]:
            entry["tailored_at"] = u["tailored_at"]
        current[u["job_url"]] = entry

    print(f"Writing {len(updates)} update(s) to Firestore...")
    try:
        patch_job_status(current)
    except Exception as e:
        print(f"ERROR: Firestore write failed: {e}")
        sys.exit(1)

    for u in updates:
        print(f"  OK  {u['job_id']}  {u['job_url'][:70]}")

    print("\nDone.")


if __name__ == "__main__":
    main()
