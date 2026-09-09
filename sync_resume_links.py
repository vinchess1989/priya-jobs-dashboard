#!/usr/bin/env python3
"""
Scan the private repo's Resumes/ folder, build GitHub blob URLs for every
resume/cover-letter PDF found, write input.csv, and upload to Firestore.

Usage:
    python sync_resume_links.py                  # dry-run: print what would be uploaded
    python sync_resume_links.py --upload         # upload to Firestore
    python sync_resume_links.py --upload --force # re-upload even if already in Firestore
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

PRIVATE_SLUG   = "vinchess1989/priya-jobs-private"
GITHUB_BASE    = f"https://github.com/{PRIVATE_SLUG}/blob/main/Resumes"
INPUT_CSV_NAME = "input.csv"


def find_private_repo() -> Path:
    """Locate the private repo on this machine using find_repos.py."""
    script = Path(__file__).parent / "find_repos.py"
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print("ERROR: find_repos.py failed:", result.stderr)
        sys.exit(1)
    data = json.loads(result.stdout)
    private = data.get("private")
    if not private:
        print("ERROR: Could not locate private repo. Make sure it is cloned.")
        sys.exit(1)
    return Path(private)


def scan_resumes(private_repo: Path) -> list[dict]:
    """Return list of {job_id, resume_url, cover_letter_url} for all PDF pairs found."""
    resumes_dir = private_repo / "Resumes"
    if not resumes_dir.is_dir():
        print(f"ERROR: Resumes directory not found at {resumes_dir}")
        sys.exit(1)

    entries = []
    for job_dir in sorted(resumes_dir.iterdir()):
        if not job_dir.is_dir():
            continue
        job_id = job_dir.name

        pdfs = list(job_dir.glob("*.pdf"))
        resume_file = next((p for p in pdfs if p.name.endswith("_resume.pdf")), None)
        letter_file = next((p for p in pdfs if p.name.endswith("_cover_letter.pdf")), None)

        if not resume_file and not letter_file:
            continue  # skip empty or non-PDF folders

        resume_url  = f"{GITHUB_BASE}/{job_id}/{resume_file.name}"  if resume_file  else ""
        letter_url  = f"{GITHUB_BASE}/{job_id}/{letter_file.name}" if letter_file else ""

        entries.append({
            "job_id":            job_id,
            "resume_url":        resume_url,
            "cover_letter_url":  letter_url,
            "resume_file":       resume_file.name  if resume_file  else "(none)",
            "cover_letter_file": letter_file.name if letter_file else "(none)",
        })

    return entries


def write_csv(entries: list[dict], csv_path: Path):
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["job_id", "resume_url", "cover_letter_url"])
        for e in entries:
            writer.writerow([e["job_id"], e["resume_url"], e["cover_letter_url"]])
    print(f"Wrote {len(entries)} rows to {csv_path}")


def already_uploaded(entries: list[dict]) -> set[str]:
    """Check Firestore for job IDs that already have resume_url set."""
    try:
        import requests
        PROJECT_ID = "priya-jobs-dashboard"
        url = (
            f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}"
            "/databases/(default)/documents/shared_state/job_status"
        )
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        doc = resp.json()

        with open('jobs.json', 'r', encoding='utf-8') as f:
            jobs = json.load(f)
        id_to_url = {j["id"]: j["url"] for j in jobs if "id" in j and "url" in j}

        fields = doc.get("fields", {})
        done = set()
        for job_id in [e["job_id"] for e in entries]:
            job_url = id_to_url.get(job_id)
            if job_url and job_url in fields:
                entry_fields = fields[job_url].get("mapValue", {}).get("fields", {})
                if "resume_url" in entry_fields or "cover_letter_url" in entry_fields:
                    done.add(job_id)
        return done
    except Exception as e:
        print(f"WARN: Could not check existing Firestore data: {e}")
        return set()


def main():
    parser = argparse.ArgumentParser(description="Sync all resume PDF links to Firestore.")
    parser.add_argument("--upload", action="store_true", help="Upload to Firestore after generating CSV")
    parser.add_argument("--force",  action="store_true", help="Re-upload even if already in Firestore")
    args = parser.parse_args()

    private_repo = find_private_repo()
    print(f"Private repo: {private_repo}")

    entries = scan_resumes(private_repo)
    if not entries:
        print("No resume PDFs found.")
        return

    print(f"\nFound {len(entries)} job folder(s) with PDFs:")
    print(f"{'Job ID':<12}  {'Resume':<6}  {'Cover Letter':<6}")
    print("-" * 50)
    for e in entries:
        has_r = "Y" if e["resume_url"]       else "-"
        has_l = "Y" if e["cover_letter_url"] else "-"
        print(f"{e['job_id']:<12}  {has_r:<6}  {has_l:<6}  {e['resume_file'][:40]}")

    if args.upload:
        if not args.force:
            already = already_uploaded(entries)
            if already:
                print(f"\nAlready in Firestore (skipping): {', '.join(sorted(already))}")
            entries = [e for e in entries if e["job_id"] not in already]
            if not entries:
                print("All entries already uploaded. Use --force to re-upload.")
                return

    public_repo = Path(__file__).parent
    csv_path = public_repo / INPUT_CSV_NAME
    write_csv(entries, csv_path)

    if args.upload:
        print("\nRunning upload_resume_links.py...")
        upload_script = public_repo / "upload_resume_links.py"
        result = subprocess.run(
            [sys.executable, str(upload_script), "--input", str(csv_path)],
            cwd=str(public_repo)
        )
        sys.exit(result.returncode)
    else:
        print(f"\nDry run complete. Run with --upload to push to Firestore.")
        print(f"Or manually: python upload_resume_links.py --input {csv_path}")


if __name__ == "__main__":
    main()
