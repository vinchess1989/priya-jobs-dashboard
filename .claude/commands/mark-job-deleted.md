Flag one or more jobs for removal from the dashboard by job ID — the scraper moves them from `jobs.json` to `deleted.json` itself on its next run.

The job ID(s) (and optional reason) are: **$ARGUMENTS**

Parse `$ARGUMENTS` as space-separated tokens:
- Any token matching `^[0-9a-fA-F]{8}$` (case-insensitive) is a `JOB_ID`.
- All remaining tokens, rejoined in original order, form `REASON` (free text). Default `REASON` to `"No longer accepting applications"` if none given.

If no token matches the `JOB_ID` pattern, abort and ask the user for at least one 8-character job ID (the same ID shown in the dashboard / printed by other skills).

Examples:
- `/mark-job-deleted 15c97ac3` — one job, default reason
- `/mark-job-deleted 15c97ac3 9a6988df` — two jobs, default reason
- `/mark-job-deleted 15c97ac3 Employer confirmed position filled` — one job, custom reason applied to it

---

## How this works

This does **not** touch `jobs.json` directly — that file is scraper-owned and rewritten wholesale on each scraper run (once daily, see `CLAUDE.md`). Instead it sets a `deletion_reason` field on the job's URL entry in Firestore's `shared_state/job_status` doc via `job_status_store.py` — the same doc `apply_url`/`tailor_model`/etc. already live in.

`scraper.py`'s `poll_manual_deletions()` polls that doc on its normal cycle, finds any URL with a `deletion_reason` set, moves the matching job from `jobs.json` to `deleted.json` (stamping `deletion_reason` onto the moved entry), and clears the Firestore flag once done. So marking a job here is not instant — it takes effect the next time the scraper runs. Unlike `manju_jobs`/`vineeth_jobs` (which run roughly every 12 minutes), `priya_jobs`'s scraper runs once daily via its Scheduled Task (08:00) — so a flagged job may not disappear from the dashboard until the next day, unless the scraper is also run manually in the meantime.

---

## Constants

**`PUBLIC`** — repo root. `$PUBLIC = (Get-Location).Path`

**`JOBS_JSON`** = `PUBLIC\jobs.json` — read-only lookup only, to resolve `JOB_ID` → `url`/`title`/`company`/`applied` and to confirm each ID exists.

---

## Step 0 — Pull latest jobs.json

```powershell
git -C "$PUBLIC" pull --rebase origin main
```

This skill never commits to `PUBLIC`, so no push-with-retry is needed afterward — but the lookup in Step 1 (particularly the `applied` guard) needs a fresh `jobs.json`, not a stale one.

---

## Step 1 — Resolve and guard, per JOB_ID

For each `JOB_ID` parsed above:

1. Read `JOBS_JSON`, find the entry with `id == JOB_ID`.
   - **Not found** → print `JOB_ID not found in jobs.json — skipping` and continue to the next ID.
2. Record `JOB_URL`, `JOB_TITLE`, `COMPANY`, and `job.applied`.
3. **Applied guard** — check both sources before flagging deletion, since an applied job should not be silently archived:
   - `job.applied == "yes"` in `jobs.json`, **or**
   - `python job_status_store.py get --url "JOB_URL" --field applied` returns `yes`

   If either is true, **do not proceed automatically**. Print a warning naming the job (`JOB_ID — JOB_TITLE @ COMPANY — already marked applied`) and ask the user to confirm before flagging it for deletion anyway. Skip this job unless they explicitly confirm.

---

## Step 2 — Set the deletion flag

For every `JOB_ID` that passed Step 1 (not found-missing, and not blocked by the applied guard without confirmation):

```powershell
python job_status_store.py set --url "JOB_URL" --field deletion_reason --value "REASON"
```

---

## Step 3 — Report

Print a summary table:

| Job ID | Title | Company | Status |
|--------|-------|---------|--------|
| 15c97ac3 | ... | ... | Flagged for deletion (reason: ...) |
| abc12345 | ... | ... | Not found in jobs.json |
| def67890 | ... | ... | Skipped — already applied (no confirmation given) |

Note at the end: "Scraper will move flagged jobs to deleted.json on its next run (once daily at 08:00, unless run manually sooner) — this list won't update instantly."
