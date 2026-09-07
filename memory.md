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

## Real candidate profile (filled in 2026-09-06/07)

Priya's real name is **Priyanga Ramachandran**, email `priyabkc99@gmail.com`, based in Oulu,
Finland. Sourced from two resumes in `originial resumes/` (note the misspelled folder name — kept
as-is, matches what's on disk):
- `Priya_LatestCV.pdf` — her general/master CV, used to fill `job_requirements.md`'s Candidate
  Profile and Hard Rejections (15 years in Configuration Management / Release Management / DevOps
  / embedded-firmware, most recently Topcon Healthcare then Elektrobit Automotive Finland, both
  English-language workplaces — she does not speak Finnish or Swedish).
- `Priyanga_CV_ICEYE_TechReleaseManager.pdf` — a resume she already tailored for a Technical
  Release Manager application at ICEYE. **Not yet wired into any tailoring skill** (the six
  auxiliary `.claude/commands/*.md` skills — resume tailoring, form-filling, etc. — are still
  out of scope for this project by design). Keep this file as the reference/style template
  whenever resume-tailoring automation is set up for `priya_jobs` later — it shows how she
  reframes the same underlying experience toward a release-management-titled role, which is the
  pattern any future tailoring prompt for her should follow.

`job_requirements.md` is now real (not a placeholder) — domain is DevOps Engineer / Configuration
Manager / Release Manager / Product Specialist, scope is Finland + remote-EU (deliberately chosen
over Finland-only or fully-global — she's open to EU remote roles, not just Finland-based ones).
Unlike Manju's dashboard, Priya's target seniority is Senior/Lead/Manager-level (matches her ~15
years), not entry-level — don't copy Manju's "reject Senior/Manager titles" hard-rejection pattern
here, it's inverted.

`_KEYWORD_TERMS`/`_KEYWORD_SITE_TEMPLATES`/`FIXED_SITES` in `scraper.py` were rewritten from
vineeth_jobs's semiconductor-specific config to the 4 keywords above, across LinkedIn (Finland, EU,
EU-remote), Indeed (fi.indeed.com Finland, indeed.com Remote), Jobly.fi, and a broad
workinfinland.com sweep. All the semiconductor-company career-page `FIXED_SITES` entries
(Intel/AMD/NVIDIA/etc.) and the chip-design subreddits were removed — not applicable to this
domain. Note: `platform` in `generate_targets()` only affects pagination for `linkedin`/`indeed`
(see `_page_url`); every other site name is scraped with the same generic link-harvest logic, so
adding/renaming a `FIXED_SITES` entry doesn't require new parsing code, just a working URL.

`firestore.rules`' `isAuthorized()` and `firebase_app/index.html`'s `ALLOWED_EMAILS` now include
both `munchnambiar@gmail.com` and `priyabkc99@gmail.com` — deployed 2026-09-07. Priya can now use
write-actions (e.g. "mark applied") once she signs in with Google on her own account.

## Verified end-to-end (2026-09-06/07)

Manual foreground run confirmed: scrape → LLM review (Groq rotation with per-model cooldown,
falling back to local `hermes-3-llama-3.1-8b`) → `update_git()` commit → push, all working. Fixed
one bootstrap issue along the way: `update_git()`'s `git add` includes `deleted.json`, which only
gets created on the *first* delete event — since that hadn't happened yet, `git add` failed with
exit 128 (pathspec matches zero files) until an empty `deleted.json` (`[]`, same
`json.dump(..., indent=2)` + `encoding="utf-8", newline="\n"` convention as everything else) was
created manually once. GitHub Pages (`https://vinchess1989.github.io/priya-jobs-dashboard/jobs.json`)
and Firebase Hosting (`https://priya-jobs-dashboard.web.app`) both confirmed serving live data.

## Open/unresolved

- The six auxiliary `.claude/commands/*.md` skills (resume tailoring, form-filling, etc.) still
  haven't been adapted for this project — core scrape+review+dashboard pipeline only, by design
  for now. When they are, use `Priyanga_CV_ICEYE_TechReleaseManager.pdf` (see above) as the
  tailoring reference/style template.
- Full authenticated dashboard testing (a write action like "mark applied", signed in as
  `priyabkc99@gmail.com`) still needs Priya to do it herself — not something to automate via
  browser automation with her credentials.
- No Windows Scheduled Task installed yet (`setup_windows_scheduler.bat` exists but hasn't been
  run) — intentionally last in the setup sequence, only after the scraper has been verified
  working manually at least once (now done — see above).

---
Last updated: 2026-09-07
