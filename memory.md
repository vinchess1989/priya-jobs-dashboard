# Project Memory — priya_jobs

Durable, cross-session knowledge specific to this project. Set up 2026-09-06 as a third
job-finder automation alongside `manju_jobs` (Finnish generalist roles) and `vineeth_jobs`
(global VLSI/semiconductor roles) — see [../manju_jobs/memory.md](../manju_jobs/memory.md) and
[../vineeth_jobs/memory.md](../vineeth_jobs/memory.md) for the shared infrastructure this project
plugs into.

## Independent infrastructure

Unlike `vineeth_jobs` (separate repo, separate Firebase project, but was originally considered as
a shared-infra option too), this project ended up fully independent by deliberate choice after
planning surfaced that sharing a repo/Firebase project with `manju_jobs` would have caused real
ongoing problems (two daemons writing one `.git`, Firestore collections silently shared across
projects). `priya_jobs` has its own:
- GitHub repo: `vinchess1989/priya-jobs-dashboard` (public — required for GitHub Pages on GitHub
  Free; confirmed both `manju-jobs-dashboard` and `vineeth-jobs-dashboard` are also public, so
  this account is on Free regardless — repo privacy wouldn't hide the served data anyway, since
  GitHub Pages sites are publicly accessible on the internet regardless of repo visibility).
- Firebase project: `priya-jobs-dashboard` (own dedicated Firestore database, region `eur3` to
  match the other two projects; own Hosting).
- Python venv (`venv/`), not shared with `manju_jobs`/`vineeth_jobs`.

## Shared with manju_jobs/vineeth_jobs (deliberate, independent decisions)

- **`GROQ_API_KEY`**: same key as the other two scrapers (a Windows User env var, machine-wide).
  This splits an already-tight free-tier daily quota three ways — accepted knowingly. The
  existing `_call_llm_with_fallback`/`_try_cloud_provider` per-model cooldown tracking (see
  `manju_jobs/memory.md`) already degrades gracefully to local LLM when Groq's quota is
  exhausted, so this isn't a functional blocker, just something to be aware of if reviews seem
  slow.
- **Local LLM priority chain**: `OpenClaw > manju_jobs > vineeth_jobs > priya_jobs` (lowest).
  `priya_jobs/scraper.py`'s `_post_llm_with_retry` defers to both `MANJU_PRIORITY_LOCK_FILE` and
  `VINEETH_PRIORITY_LOCK_FILE` (poll-and-release, same pattern `vineeth_jobs` already used for
  just the manju lock) before ever competing for `PIPELINE_LOCK_FILE`. It claims no priority lock
  of its own — nothing is lower priority than it.
- **This required modifying `vineeth_jobs/scraper.py`** (a live production file) to add a new
  `VINEETH_PRIORITY_LOCK_FILE` it claims around its own turn, mirroring exactly how `manju_jobs`
  claims `MANJU_PRIORITY_LOCK_FILE` — previously vineeth_jobs was the lowest tier with nothing to
  signal to, so this lock didn't need to exist until now.

## Firestore document-creation gotcha (same root cause as documented in manju_jobs/memory.md)

`firestore.rules` gates `create`/`delete` on `shared_state/{docId}` behind an authenticated
`isAuthorized()` check, while `read`/`update` are open (`if true`) — this lets the unauthenticated
Python REST calls in `scraper.py` (`poll_firebase_feedback`, `poll_re_review_request`) read/update
existing documents freely, but a `PATCH` against a document that **doesn't exist yet** is
evaluated by Firestore as a `create`, which requires auth those plain `requests.patch()` calls
can't provide. Fixed by pre-creating empty placeholder documents at `shared_state/job_status` and
`shared_state/re_review_request` via the Firebase Console (each with a single `initialized: "true"`
string field, since Firestore's console UI requires at least one field to save a new document) —
done once, 2026-09-06, before the scraper's first run.

## Open/unresolved

- `job_requirements.md` is a placeholder template — real candidate background/requirements not
  yet filled in. The scraper's keyword/site-search terms (inherited from `vineeth_jobs`'s VLSI/
  semiconductor-specific search URLs) also still need replacing once Priya's real job domain is
  decided — they currently still reflect vineeth_jobs's search terms verbatim.
- `firestore.rules`' `isAuthorized()` and `firebase_app/index.html`'s `ALLOWED_EMAILS` only list
  `munchnambiar@gmail.com` (the account owner, for setup/testing) — add Priya's real email to
  both once known, or she won't be able to use any write-actions (e.g. "mark applied") on her own
  dashboard (read/update access is open regardless, so browsing isn't blocked).
- The six auxiliary `.claude/commands/*.md` skills (resume tailoring, form-filling, etc.) haven't
  been adapted for this project — core scrape+review+dashboard pipeline only, by design for now.
- No Windows Scheduled Task installed yet (`setup_windows_scheduler.bat` exists but hasn't been
  run) — intentionally last in the setup sequence, only after the scraper has been verified
  working manually at least once.

---
Last updated: 2026-09-06
