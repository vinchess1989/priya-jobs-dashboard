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
Firebase project (`priya-jobs-dashboard`, own dedicated Firestore database), own venv. It does
**not** have manju_jobs's `jobs.json` merge-driver/git-hygiene apparatus (`git_jobs_merge_driver.py`,
`setup_merge_driver.ps1`, `.gitattributes`, `job_status_store.py`) — with no shared `.git` and no
Firestore data shared with anyone else, that machinery isn't needed here. `jobs.json` is still
scraper-owned (rewritten wholesale each run) but since nothing else writes to it, there's no
conflict to avoid.

It **does** share the local LLM server infrastructure with `manju_jobs`/`vineeth_jobs` — see
`memory.md` for the priority-lock mechanism and why `vineeth_jobs/scraper.py` had to be modified
to support this.

## Out of scope (for now)

The six auxiliary `.claude/commands/*.md` skills from `manju_jobs` (resume tailoring, form-filling,
add-job-link, find-apply-link, mark-job-deleted, test-and-publish) have not been adapted for this
project — only the core scrape+review+dashboard pipeline exists here.
