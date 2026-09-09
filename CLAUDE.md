# CLAUDE.md

Instructions for any Claude Code session working in this repo.

## Read and maintain `memory.md`

`memory.md` (this directory) holds durable, cross-session project knowledge. Read it at the
start of any nontrivial work here — it explains this project's relationship to `manju_jobs` and
`vineeth_jobs` (shared local LLM server, shared `GROQ_API_KEY`, priority-lock chain) even though
this repo, Firebase project, and GitHub repo are all fully independent.

Update `memory.md` whenever you learn something a future session would need — a new gotcha, a
fixed bug with a non-obvious cause, a changed architecture/config. Don't log routine work.

## What's different from `manju_jobs`/`vineeth_jobs`

This repo is a genuinely independent sibling — its own GitHub repo (`priya-jobs-dashboard`), own
Firebase project (`priya-jobs-dashboard`, own dedicated Firestore database), own venv, own private
resume repo (`vinchess1989/Priya-jobs-private` — note: a **different account** than Manju's private
repo, which lives under `munchnambiar`; don't assume the same account for both). It does **not**
have manju_jobs's `jobs.json` merge-driver apparatus (`git_jobs_merge_driver.py`,
`setup_merge_driver.ps1`, `.gitattributes`) — with no shared `.git`, that specific machinery isn't
needed here. `jobs.json` is still scraper-owned (rewritten wholesale each run) but since nothing
else writes to it, there's no merge conflict to avoid.

It **does** have its own `job_status_store.py` (pointed at the `priya-jobs-dashboard` Firestore
project) — this isn't for merge-conflict avoidance like in manju_jobs, it's the generic per-job
metadata store (`apply_url`, `apply_email`, `deletion_reason`, `action_item`, `form_filled`, etc.)
that `find-apply-link`, `mark-job-deleted`, and `fill-form` all read/write through. Needed
regardless of whether `.git` is shared with anyone.

It **does** share the local LLM server infrastructure with `manju_jobs`/`vineeth_jobs` — see
`memory.md` for the priority-lock mechanism and why `vineeth_jobs/scraper.py` had to be modified
to support this.

## Out of scope (for now)

`tailor-resume`, `fill-form`, `find-apply-link`, and `mark-job-deleted` are now adapted for this
project (ported from `manju_jobs` — see `memory.md` for what changed). Still not ported:
`add-job-link.md`, `test-and-publish.md`, and the `email-apply` skill — none of these are
dependencies of the four skills above; port them separately if/when needed.
