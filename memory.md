# Project Memory — priya_jobs

## Importing a LinkedIn search Priya ran herself (2026-09-30)

- The logged-in `linkedin.com/jobs/search-results/` UI has no job ids in its links: cards are
  `div[role=button]`; clicking one sets `currentJobId=` in the URL. Collect ids by clicking every card
  per page (`start=0,25,...`, scroll the list first).
- Public URL in the scraper's own format (`fi.linkedin.com/jobs/view/<slug>-<id>`, no query) comes from
  the guest API `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<id>` (topcard link) - no login,
  ~1 req/s was fine for 75 ids.
- Dedupe by LinkedIn job id against `jobs.json`, `deleted.json` and `seen_urls.json`, then append as
  `matches_requirements: "pending"` (id = md5(url)[:8], `source: linkedin_manual_search_oulu_it`); the
  scraper's review loop rates pending jobs first. 75 added this way survived the next scraper push.

## Safety-critical / functional-safety search terms; manual jobs get wiped (2026-09-29)

- Priya asked (after tailoring for a manually-shared ALTEN "Requirement Engineer – Safety Critical
  Software" role, id `a17e0001`) that similar jobs be found automatically. Added `_KEYWORD_TERMS`
  "Functional Safety", "Safety Critical", "IEC 61508", "ISO 26262" and a matching Domain/Role bullet in
  `job_requirements.md` (yes for requirements/traceability/certification-support work under any FuSa
  standard; "maybe" when the core job is a certified FuSa-engineer credential or FMEA/FTA/HAZOP/SIL work).
  The scraper runs on Vineeth's PC, so the new terms apply after it pulls and restarts.
- **Hand-added `jobs.json` entries are fragile.** The manual Nokia job `de5e0002` vanished in the
  2026-09-23 22:39 auto-update from Vineeth's PC (a stale-copy rewrite: -24.8k lines across
  `jobs.json`/`jobs_history.json`), and the scraper's `git pull --rebase -X theirs` resolves any
  `jobs.json` conflict in favour of its own copy. After adding a manual job (`source: "manual_add"`),
  re-check it survives the next scraper push; `a17e0001` uses the placeholder URL
  `manual://alten-finland/requirement-engineer-safety-critical/a17e0001` (no public posting exists).

## Firestore locked down; Python scripts use a service account (2026-09-28)

`firestore.rules` used to leave `shared_state` / `user_feedback` readable and updatable by
anyone (`if true`) so the unauthenticated Python REST calls could write. Now every collection is
allow-listed accounts only, and scripts authenticate via **`firestore_auth.py`** (`session()` returns a
`google.auth` `AuthorizedSession` with the project's service account; SA requests bypass rules via IAM).
- Key file: `~/.secrets/priya-jobs-dashboard-sa.json` - OUTSIDE the repo (Firebase Console -> Project settings ->
  Service accounts -> Generate new private key). `.gitignore` blocks `*firebase-adminsdk*.json` / `*-sa.json`.
- **Any other PC** running scripts or skills that touch Firestore (tailor-resume, fill-form,
  find-apply-link, mark-job-deleted) needs its own key at that path plus `pip install google-auth`
  in the venv - otherwise `firestore_auth.session()` raises FileNotFoundError.
- New Firestore calls must use `firestore_auth.session().get/patch(...)`, never bare `requests` - a bare
  call now gets 403. Rules deploy: `firebase deploy --only firestore:rules` from `firebase_app/`.
- Key access (2026-09-28): priyabkc99@gmail.com has roles/firebase.viewer + roles/iam.serviceAccountKeyAdmin on this project, so Priya can generate her own PC key in the console (no DB/rules/hosting rights). Owner: vineethkaimal1989@gmail.com.


## `/fill-form dead<10` batch, venv missing `anthropic`, Wartsila SuccessFactors gate (2026-09-28)

- Tailored and pushed resumes/cover letters for 7 jobs selected from a `dead<10` discovery
  checklist: Teleste Quality Manager (`5ea0256a`), Nordea Application Reliability Lead (`941acc82`),
  Nordea Lead Project Leader (`6372ef14`), If Insurance CRM & MarTech Platform Specialist
  (`301c5827`), Tana Oy Digital Solutions Engineer (`f89f108c`), Fortum Digital Development
  Specialist (`48d3c245`), Wartsila Maintenance Processes and Tools Expert (`82f669f2`).
- **`scrape_application.py` needs the `anthropic` package**, which was not in the original venv
  setup list (playwright, requests, python-dotenv, beautifulsoup4, filelock, pytest) — installed it
  ad hoc with `pip install anthropic`. Add it to the standard install list for future fresh setups.
- **Wartsila's careers portal is a genuine account-creation gate, not a scraping bug** — see the
  new `careers.wartsila.com` entry in `site_patterns.json`: clicking the job page's "Apply now"
  link always lands on `career2.successfactors.eu`'s bare sign-in/create-account page with the
  specific job context lost; there is no pre-login job-specific application form to scrape. Same
  category of blocker as LinkedIn needing Priya's own sign-in — flagged to her, not worked around.

Durable, cross-session knowledge specific to this project. Set up 2026-09-06 as a third
job-finder automation alongside `manju_jobs` (Finnish generalist roles) and `vineeth_jobs`
(global VLSI/semiconductor roles) — see [../manju_jobs/memory.md](../manju_jobs/memory.md) and
[../vineeth_jobs/memory.md](../vineeth_jobs/memory.md) for the shared infrastructure this project
plugs into.

## Dashboard: "Added" column, days-ago filter & clickable Today card (2026-09-27)

- **"Added" column added to table:**
  - Placed at column index 7 (between "Posted" and "Deadline").
  - Formatted using `formatAddedDate(dateStr)` using local date calculation (`localDateStr(d)`) to avoid timezone rollbacks in Finland (UTC+3): displays "Today", "Yesterday", or "DD Mon YYYY", with full ISO string & time in tooltip.
  - Sorting (`sortTable(7)`): parses timestamp chronologically (supports ISO `added_at` strings), placing `'N/A'` or empty dates at the bottom.
  - Mobile card view: updated mobile CSS grid template (`"posted added deadline"` in one row) with `::before` pseudo-element `"Added"`.
- **Days-ago filter for "Added" column:**
  - Input `#added-days-filter` with quick chip buttons: `Today` (0d), `3d`, `7d`, `14d`, `30d`.
  - Filter logic in `filterTable()` checks `diffDays = Math.round((todayUtcMs - addedMs) / DAY_MS)` such that `diffDays >= 0 && diffDays <= addedLimit`. Entering `7` filters jobs added in the last 7 days; `0` matches jobs added today.
  - Persisted in localStorage (`addedLimit`).
- **Interactive Today Card ("New matching in Finland today"):**
  - Added click handler `window.filterTodayFinlandJobs('all')` on the stat card.
  - Automatically resets conflicting column filters, sets `#added-days-filter` to `0`, `#location-text-filter` to `Finland`, checks `yes` and `maybe` in the Matches filter, re-filters the table, and smoothly scrolls to `#jobs-table`.
  - Location filtering enhanced so when `locationNeedle` is `finland` or `suomi`, it also matches any job flagged `isFinlandJob` (even if the raw text is e.g. "Helsinki Metropolitan Area" or "Espoo").

## Sibling board `priya_global_jobs` + new PRIYA_PRIORITY_LOCK_FILE (2026-09-27)

Jobs outside Finland live on a **separate board**, not this one: `C:\Users\vinee\priya_global_jobs`
(GitHub `vinchess1989/priya-global-jobs-dashboard`, Firebase `priya-global-jobs`,
https://priya-global-jobs.web.app, task `PriyaGlobalJobsLocalLLMOrchestrator`). It uses the
**local LLM only** and is the lowest priority on it, so this scraper now **claims
`PRIYA_PRIORITY_LOCK_FILE`** (`~/.claude/scraper_priya_priority.lock`) around its local POST,
mirroring vineeth_jobs. The chain is OpenClaw > manju > vineeth > priya > priya-global. This board's
scope (Finland + remote) and `job_requirements.md` are unchanged. The global scraper skips any
URL (LinkedIn: job ID) already in this repo's `jobs.json`, so this file is read by a sibling.
Each dashboard header links to the other. The lock change takes effect when this scraper next
restarts; it was stopped at the time, while another session ran an LLM benchmark.
Gotcha: `git status` showing "ahead N" here is usually harmless. With `GITHUB_TOKEN` set,
`update_git()` pushes to a token URL, not to `origin`, so `origin/main` is never updated. Check with
`git ls-remote origin refs/heads/main`.

## Trial: English-speaking countries (excl. US) site probe (2026-09-27)

User wants scope widened to Tier 1 (UK, IE, CA, AU, NZ) + Tier 2 (SG, IN, ZA, MT, HK), **any
work model** (on-site/hybrid abroad OK → `job_requirements.md` "not relocating" line must go when
wired in). Read-only probe (scratchpad script, own target list, 2 keywords, 1 page, home PC):
- **LinkedIn works in every country** (70–90 relevant jobs/search), except `location=Malta`,
  which resolved to Ohio. It needs a geoId. LinkedIn started returning **429** after ~60 rapid
  requests, so adding 10 countries × 12 keywords risks throttling.
- **Works (real job links):** Reed (~22/page), Totaljobs (~20; CWJobs serves the same Totaljobs
  listings, so it's a duplicate), CV-Library (~21, 403 on the 2nd query), IrishJobs (~17; Jobs.ie is
  the same StepStone group with ~4), Job Bank CA (~25), Jora AU (~15), CareerJunction ZA (~26,
  `-job-NNN.aspx` URLs), PNet ZA (`...-inline.html`). **`parse_generic` rejects the last two**
  (`.html` skip / no `/job` segment), and admits lots of nav/category noise on Reed/Totaljobs/
  IrishJobs (`/jobs/<kw>/in-<town>` location facets). Per-site patterns are needed before going live.
- **Weak:** Guardian Jobs (ignores the keyword, returns academic/charity jobs), Trade Me (irrelevant
  hospitality results), JobsInMalta (keyword ignored, whole small market; OK as a broad sweep).
- **Blocked/empty:** every Indeed country (Cloudflare), Seek AU/NZ, JobStreet SG, JobsDB HK
  (Cloudflare), Naukri + Foundit (Access Denied), Adzuna UK/CA/AU (403/429), CTgoodjobs, KeepMePosted
  (captcha), Eluta, Careers24, MyCareersFuture (JS-rendered, 0 real links), JobsPlus MT (404).
- Volume warning: 2 keywords already produced ~2,000 unseen links. The LLM review is the
  bottleneck, so the backlog will grow a lot.

## Trial: running the scraper off this PC via GitHub Actions (2026-09-27)

Goal: stop depending on this PC / LM Studio (Groq + Gemini now work). Firebase itself can't
host it (Functions: Blaze plan + 9–60 min limit + heavy Chromium); GitHub Actions is free and
unlimited for this public repo. First step was a **read-only probe** on branch `actions-probe`
(never merged to `main`): `actions_probe.py` reuses `scraper.generate_targets()` + parsers, and
writes only `probe_out/`, which is uploaded as an artifact; `.github/workflows/scrape-probe.yml` runs on push to
that branch / manual dispatch. No commits, LLM or Firestore — live scraper unaffected. Made in a
separate git worktree so the live scraper's working copy on `main` is never switched. Pushing
the workflow file worked despite `gh auth status` showing only the `repo` scope.
Blockers still open before a real cloud run: `review_pending_jobs` bails if `LOCAL_LLM_ENDPOINT`
is unset (needs a cloud-only mode); main loop never exits (needs a one-cycle mode for the 6h job
limit); `orchestrator.py`/`setup_windows_scheduler.bat` hardcode `C:\Users\vinee\priya_jobs`;
only one machine may run the scraper at a time (push uses `-X theirs` → last writer wins).
Home-PC baseline per target: `Found N` lines in `logs/scraper_*.log`.
**Probe result (run 36276109211, 169 targets, ~40 min):** LinkedIn (all 4 families) works fully
from Actions — 90 targets compared, Actions found at least as many links as home in every
family. Työmarkkinatori and Work in Finland work.
**Duunitori and Jobly are blocked** by a Cloudflare "Just a moment..." challenge (work at home) —
~28 of ~290 lifetime "yes" jobs came from them. Indeed returns 403 + Cloudflare challenge from
Actions, **but is mostly blocked at home too**: nearly every Indeed target logs exactly
"Found 3" = the challenge page's links, not jobs (only occasional real pages). Kuntarekry 0
both places (known, see below); MeetFrank times out both places.

## Requirements Engineer / Quality Engineer / MDR / ISO 13485 keywords added (2026-09-25)

`_KEYWORD_TERMS` in `scraper.py` now also includes `"Requirements Engineer"`, `"Quality
Engineer"`, `"EU MDR"`, and `"ISO 13485"`, at Priya's request. All four are genuine fits, not
stretches: Requirements Engineer matches her real requirements-traceability ownership at Topcon
and Elektrobit; Quality Engineer matches her quality-gatekeeper role at Topcon and ASPICE/TUV
audit support at Elektrobit; MDR/EU MDR and ISO 13485 are the exact regulated-medical-device
standards she worked under hands-on at Topcon. `job_requirements.md`'s Target Job Criteria §1 got
two new Domain/Role Type bullets (Requirements Engineer, Quality Engineer) plus an expanded
"Especially strong fit" line calling out MDR/ISO-13485-centered roles specifically. Same mechanism
as the 2026-09-22 Technical Writer/Documentation Specialist addition below — no `FIXED_SITES`
changes needed, new `_KEYWORD_TERMS` entries automatically cross with every existing site template.
Takes effect on the scraper's next run. **Correction (same day):** an earlier version of this
note said no scheduled task exists — wrong. The Windows Scheduled Task is named
`PriyaJobsLocalLLMOrchestrator` (runs `venv\Scripts\python.exe orchestrator.py`, which spawns
`scraper.py`); a search for "priya_jobs"/"scraper" in task names misses it. Restart with
`Stop-ScheduledTask` / `Start-ScheduledTask -TaskName PriyaJobsLocalLLMOrchestrator`.

## Location scope narrowed, Today card, scrape starvation fix (2026-09-25)

- **Location scope** (Priya's request): Finland (any work model) OR fully remote and open to
  someone in Finland (worldwide / EU / Europe / EMEA / Nordics), never US. On-site/hybrid in other
  EU countries is now a hard "no" (was "maybe"). `job_requirements.md` Hard Rejections + §2 rewritten;
  scraper's on-site `linkedin_eu` template replaced by `linkedin_ww_remote` (location=Worldwide&f_WT=2).
  Keywords added: "Requirements Manager", "Quality Manager".
- **Scrape starvation bug:** main loop only scraped when zero jobs were pending review; a
  requirements change re-queues ~4,200 jobs, so no new jobs were scraped 2026-09-18 → 09-25. Now a
  scrape pass runs at least every `SCRAPE_INTERVAL_SECONDS` (3600) regardless, and review batches take
  never-evaluated jobs before re-reviews.
- **`added_at`** (new per-job field, ISO with offset): stamped by the scraper when a job is first
  seen; backfilled for existing jobs from the first `jobs.json` commit containing each URL
  (`git cat-file --batch` over all commits, regex on `"url"`). Drives the dashboard **Today card**:
  new yes-matches in Finland added today (location regex of Finnish places, or Finland-only source
  with unknown location) + applications today (`shared_state/job_status[url].applied_date == local
  today`). The dashboard Applied dropdown now writes `applied_date` (it didn't before); review.html
  now writes it in LOCAL date (was UTC via toISOString — wrong between 00:00–03:00 Finnish time).
- Dashboard: Location column free-text "contains" filter (`#location-text-filter`, AND-ed with the
  checkboxes, persisted as `locationText`). History charts got a Detail/Day/Week/Month switch
  (`_chartGranularity`, end-of-period snapshot per bucket, applied = period max); review.html's
  Auto-Submitted bar chart got Day/Week/Month (counts summed).

## Mobile app shell on firebase_app/index.html (2026-09-25)

Below `(max-width: 768px), (max-height: 550px) and (orientation: landscape)` the dashboard renders
as an app: sticky compact header, fixed bottom nav (Jobs / Filters / Status / Review→review.html),
views driven by `body.mview-{jobs,filters,status}` via `setMobileView()`. All CSS is one appended
block at the end of `<style>`; desktop rules untouched (verified: desktop layout geometry identical
before/after at 1440×900). Gotchas: table rows become cards via CSS grid on `tbody tr` — must NOT
use `!important` on that `display`, since `renderPage()` pages rows with inline `display:none`.
Filters view turns both `<thead>` rows into a label|filter grid (`tr {display:contents}` + per-column
`grid-row`). `body` uses `overflow-x: clip` (not hidden) or every sticky bar breaks. The mobile block
overrides the old "max-content zoom-out in landscape" rule for phones.
`review.html` got the same shell (2026-09-25): bottom nav = its 4 tabs (via existing `switchTab()`,
which now calls `onMobileTabChange`) + Board link, with tab-count badges; rows → cards labelled
generically by `labelMobileCells()` from each table's header text (`data-label`/`data-role`),
re-run by a MutationObserver on `<main>`; filter row → scrolling chip strip; `.ms-dropdown` pinned
to screen edges. Gotcha: panels keep the `spinner-container` class after rendering (4rem padding) —
neutralised on mobile with `:has()`. Both pages deployed to Firebase Hosting 2026-09-25
(`firebase deploy` from `firebase_app/`; firestore.rules was already current).
Verified with a Playwright harness that stubs `window.firebase` (auto sign-in) and loads the live
GitHub Pages `jobs.json`, then runs the mobile-app-shell skill's `audit_mobile.js`.

## Dashboard "Stale Data" — scraper stuck committing to a detached HEAD (found + fixed 2026-09-24)

The dashboard's ⚠️ Stale Data badge fires when GitHub Pages' `jobs.json` `Last-Modified` is >24h
old (`firebase_app/index.html` `onDataLoaded`). On 2026-09-23 22:19 a manual publish did
`git pull --rebase` that stopped at step 1/30 and was left unfinished (`.git/rebase-merge`
present). The long-running scraper kept committing every cycle onto the resulting detached HEAD,
and its bare `git push` failed every time with a misleading "Check your GITHUB_TOKEN" message, so
nothing reached `origin/main` for ~37h while the scraper looked healthy. Restarting the scraper
does NOT fix this — check `git status -sb` for `HEAD (no branch)` / "rebasing" first.
Recovery used: stop priya orchestrator+scraper, commit working tree, `git branch scraper-detached`,
`git rebase --abort`, merge `scraper-detached` into `main` (merge, not rebase — replaying 30
`jobs.json` commits conflicts on every step), take the scraper side for all generated files
(`jobs.json`, `job_descriptions/*`, `checkpoint.json`, `deleted.json`) — main-side `jobs.json`
diffs were only re-review flags/re-evals, which the requirements-hash check regenerates — push,
restart. Hardening in `scraper.py`'s git step: skips (with a clear ERROR) when a rebase is in
progress or HEAD is detached; does `git pull --rebase -X theirs <remote> <branch>` before pushing
(aborting on any failure so it can't strand a rebase itself); prints git's real error (token
masked). Nothing pulled before pushing previously, so any remote commit (e.g. tailor-resume's
"Update resume links") also made pushes fail. Not yet ported to manju_jobs/vineeth_jobs.

## Vinjey Software Systems experience entry was missing from every resume (2026-09-23)

`job_requirements.md` has always listed **Vinjey Software Systems (Nov 2012 – Dec 2013)** as part
of Priya's real background ("earlier hands-on software engineering — C#, C++, embedded/DSP"), but
this fifth, earliest role was never actually added to `master_data.json` when the candidate
profile was first filled in (2026-09-06/07) — the template only ever had four experience entries
(Topcon, Elektrobit, Accenture, Pronto). That meant it was silently missing from every tailored
resume generated before 2026-09-23, twelve jobs' worth. Priya caught this herself and asked where
it was. Fixed by asking her for the job title (she said "Software Engineer, similar to Pronto")
and adding a fifth entry to the master template — title "Software Engineer", company "Vinjey
Software Systems", dates "Nov 2012 – Dec 2013", two bullets about embedded/DSP software in C/C++
(the exact scope `job_requirements.md` names, nothing further invented) — placed after Pronto as
the earliest role. Retrofitted into all 12 already-tailored resumes' `*_data.json` files, then
regenerated and visually re-verified each PDF still fits one page. `tailor-resume.md`'s
`resume.experience` rule and "Priya's profile" facts list were both updated from "four entries" to
"five entries" so future runs include Vinjey automatically. **If a future session notices any other
fact in `job_requirements.md`'s Candidate Profile that isn't reflected in `master_data.json`,
treat it the same way — check, don't assume the template is complete.**

## Documentation-domain keywords added (2026-09-22)

`_KEYWORD_TERMS` in `scraper.py` now includes `"Technical Writer"` and `"Documentation
Specialist"` alongside the original four (DevOps Engineer, Configuration Manager, Release
Manager, Product Specialist), at Priya's request to widen the dashboard to documentation-focused
roles. This is a genuine fit, not a stretch — she has real Technical Writing & Translation
Specialist (Accenture) and Technical Communication Specialist (Topcon) experience, both already
used heavily in tailored resumes. `job_requirements.md`'s Domain/Role Type criteria (Target Job
Criteria §1) got a matching new bullet so the LLM screening step scores these correctly instead of
treating them as off-domain. No `FIXED_SITES` or site-template changes needed — new
`_KEYWORD_TERMS` entries automatically get crossed with every existing site template (LinkedIn
FI/EU, Indeed, Jobly, etc.) via `generate_targets()`. Takes effect on the scraper's next run (once
daily at 08:00, or sooner if run manually).

## Major Features
1. **Priya's Job Search Automation:** Scrapes customer success, administrative, hospitality, and educational opportunities across Finland and remote portals.
2. **Hybrid Cloud & Local LLM Scoring:** Evaluates applicant match score using Groq API (`llama-3.3-70b-versatile`) with seamless local fallback to LM Studio.
3. **Standalone Dedicated Firebase Project:** Fully independent Firestore database and Firebase Hosting (`priya-jobs-dashboard`).
4. **Automated Application Tracker:** Live status columns tracking applied dates, response rates, and follow-ups.
5. **Resume & Cover Letter Generator:** Automatically builds tailored resumes stored in `Priya_jobs_private/`.

## Minor Features & Utilities
- **Daily Quota Cooldown Management:** Proactively rotates between cloud and local inference to respect free-tier rate limits.
- **Deleted Jobs Archive:** Separate JSON storage ensuring discarded postings aren't re-scraped.
- **Direct Application Deeplinks:** Instant launch buttons directly into corporate ATS portals.

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
  This splits an already-tight free-tier daily quota three ways. **Since 2026-09-26** the scraper
  prefers `PRIYA_GROQ_API_KEY` from this repo's `.env` (gitignored; created with an empty value)
  — a key from a *separate Groq account* gives priya_jobs its own quota. It must be a different
  variable name: `load_dotenv()` never overrides the existing `GROQ_API_KEY` user env var. Empty =
  falls back to the shared key. Why it matters (measured 2026-09-25): failed Groq calls cost ~0.4s
  (429s return instantly), while priya's local calls queue behind manju/vineeth — 8,009 local vs
  1,139 Groq verdicts in the log. So Groq-first is right; more Groq quota is the lever, not
  local-first.
- **Gemini as second cloud provider (2026-09-27):** order is Groq → Gemini → local.
  `PRIYA_GEMINI_API_KEY` in `.env`, `GEMINI_MODELS` default `gemini-3.1-flash-lite,gemini-3.5-flash-lite`
  via Google's OpenAI-compatible endpoint (reuses `_try_cloud_provider`). `gemini-2.5-flash-lite` is
  closed to new accounts (404). Validation on 40 jobs: 3.1-flash-lite 32/40 exact (37/40 yes≈maybe),
  all rejections right, slightly strict on borderline; 3.5-flash-lite 28/40. Live result: 60 of 63
  verdicts in the first ~7 min came from Gemini (~1.5s each) vs ~3–5 min/job locally. Free tier is
  capped per model per day (~500 requests each, Google adjusts it), so under backlog it exhausts
  within hours and falls through to local; paid tier ≈ $0.25/$1.50 per 1M tokens. Free-tier prompts
  may be used by Google for training (prompt includes Priya's profile).
- **PRIYA_GROQ_API_KEY turned out to be in the SAME Groq organization** as the shared key (daily
  request counter 999→998→997→996 alternated across both keys), so it adds no quota. A second key
  only helps if created under a different Groq account.
- **Local calls use `max_tokens=4096` up front** (`LOCAL_LLM_MIN_MAX_TOKENS`, 2026-09-26). gemma-4
  (reasoning) exhausted the 500-token budget on nearly every job, which cost a wasted first pass
  plus a 4096 retry each time (578 retries in the log). HTTP timeout for that budget is
  `LOCAL_LLM_BIG_BUDGET_TIMEOUT` = 900s: LM Studio server log (`~\.lmstudio\server-logs\`) showed
  gemma-4 generating at only ~6.5 tok/s (prompt processing ~190 tok/s, ~7.3k-token prompts ≈ 40s),
  so 4096 tokens ≈ 670s — the old 480s turned long reasoning into ReadTimeouts. The ~6.5 tok/s
  generation speed is the real bottleneck for all three dashboards.
- **Restarting:** `Stop-ScheduledTask PriyaJobsLocalLLMOrchestrator` kills the orchestrator but
  NOT the child `scraper.py` — kill that PID too, or the new scraper refuses to start (single-
  instance lock) and the old code keeps running.
- **Local LLM priority chain**: `OpenClaw > manju_jobs > vineeth_jobs > priya_jobs` (lowest).
  `priya_jobs/scraper.py`'s `_post_llm_with_retry` defers to both `MANJU_PRIORITY_LOCK_FILE` and
  `VINEETH_PRIORITY_LOCK_FILE` (poll-and-release, same pattern `vineeth_jobs` already used for
  just the manju lock) before ever competing for `PIPELINE_LOCK_FILE`. It claims no priority lock
  of its own — nothing is lower priority than it.
- **This required modifying `vineeth_jobs/scraper.py`** (a live production file) to add a new
  `VINEETH_PRIORITY_LOCK_FILE` it claims around its own turn, mirroring exactly how `manju_jobs`
  claims `MANJU_PRIORITY_LOCK_FILE` — previously vineeth_jobs was the lowest tier with nothing to
  signal to, so this lock didn't need to exist until now.

## GitHub "secret detected: Google API Key" alerts are the Firebase web config key (2026-09-28)

GitHub secret scanning flagged `firebase_app/review.html` (apiKey line) in the sibling repo
`priya-global-jobs-dashboard` (commit aec0dbca). Checked: it's the Firebase **web config** apiKey
(same value as in `index.html`), which is public by design — it ships to every browser that loads
the live site, so rotating it achieves nothing. The Gemini/Groq keys are NOT in either repo's
history or tracked files (`.env` is gitignored). Such alerts can be dismissed as false positives.
Optional hardening: restrict that key in Google Cloud Console (HTTP referrers = the web.app /
firebaseapp.com domains; API restrictions = Firebase/Identity Toolkit/Firestore).
**The real exposure is the Firestore rules below**: `shared_state` and `user_feedback` allow
`read, update: if true`, so anyone with the (public) project ID + key can read Priya's application
status/notes and overwrite fields without signing in. Deliberate trade-off so the unauthenticated
Python REST scripts (`scraper.py`, `job_status_store.py`, `sync_resume_links.py`,
`upload_resume_links.py`, `scrape_application.py`) can write. Proper fix: give those scripts a
service-account credential and require auth in the rules — same change needed in manju_jobs,
vineeth_jobs and priya_global_jobs.

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

## Finnish site coverage added to parity with manju_jobs (2026-09-08)

`scraper.py` now mirrors manju_jobs's Finnish site list (LinkedIn, Duunitori, Indeed.fi, Jobly.fi,
Kuntarekry, Työmarkkinatori, MeetFrank, Work in Finland — see `_KEYWORD_SITE_TEMPLATES`/
`FIXED_SITES`), scoped to Finland + remote-EU instead of manju's Worldwide-remote. This required
two real fixes, not just copying URLs:
- `parse_generic()`'s job-URL keyword allowlist was English-ATS-only (inherited from vineeth_jobs)
  and silently dropped every Finnish site's links. Added `/tyopaikka`, `/tyopaikat/tyo/`,
  `/avoimet-tyopaikat` (with matching skip patterns for category/browse/filter pages and
  Duunitori's `/lisaa_suosikkeihin` favorite-toggle duplicate links).
- **Duunitori's real job-detail path is specifically `/tyopaikat/tyo/<slug>`**, not the plain
  `/tyopaikat/` substring used at first — that broader pattern also matched Duunitori's own
  category pages (`/tyopaikat/ala/...`) and browse pages (`/tyopaikat/selaa`), which don't contain
  real postings. Verified fix live: `duunitori_devops_engineer` returns 21 real, on-domain
  DevOps Engineer postings.
- Duunitori's job listing content only appears after ~10+ scroll iterations (lazy-loaded); a
  quick 4-scroll test looked completely broken (nav-only links) but the production
  `scroll_count: 12` config was fine all along — don't re-diagnose this as broken from a
  short manual scroll count.
- **Kuntarekry currently yields 0 results** even after accepting its Cookiebot consent banner —
  its "Tulokset" (Results) section renders structurally empty in headless Playwright (likely a
  third-party Talentech widget that doesn't fire under these conditions). Left in the config for
  parity since a 0-yield site is harmless, but don't spend more time re-debugging this without a
  new lead — plain cookie-dismiss + scroll doesn't fix it.

The initial semiconductor-domain test data (90 stale jobs.json entries, 69 job_descriptions files,
seen_urls.json, jobs_history.json) was wiped on 2026-09-08 since none of it matched Priya's actual
domain — see the commit "Add manju_jobs's Finnish site list and reset stale semiconductor test
data". `checkpoint.json`'s `target_index` was reset to 0 for the new target list.

## tailor-resume / fill-form skills ported from manju_jobs (2026-09-09)

`priya_jobs` now has working `tailor-resume`, `fill-form`, `find-apply-link`, and
`mark-job-deleted` skills, ported from `manju_jobs` for Priya specifically, to be run by **Priya
herself from her own separate PC** — not operated remotely from this machine. Key facts a future
session needs:

- **Private resume repo:** `vinchess1989/priya-jobs-private` (public dashboard's own account) —
  **note this diverges from Manju's convention**, whose private repo (`Manju-jobs`) lives under a
  *different* account, `munchnambiar`. Don't assume both candidates' private repos live under the
  same account. Cloned locally as a sibling: `c:\Users\vinee\Priya_jobs_private\` — note the local
  **folder** name uses `Priya_jobs_private` (underscores, capital P) while the actual **GitHub**
  repo is `priya-jobs-private` (hyphens, lowercase); this mismatch is harmless (the local folder
  name is never compared against the remote slug — `find_repos.py` locates it by matching the git
  remote URL, not the directory name) but don't "fix" the local folder name expecting it to need
  to match, and don't typo the GitHub slug's casing when referencing it directly (e.g. in a browser
  URL — GitHub redirects case-insensitively, but scripts/config should use the exact real casing).
- **Support scripts copied + adapted** from `manju_jobs`, each with the Firestore project ID /
  private-repo slug swapped to Priya's own (`priya-jobs-dashboard` / `vinchess1989/priya-jobs-private`):
  `make_resume.py`, `html_to_pdf.py` (unchanged), `job_status_store.py` (new — priya_jobs didn't
  have one before this; see `CLAUDE.md` for why it's needed despite no shared `.git`),
  `sync_resume_links.py`, `scrape_application.py`, `find_repos.py`, `upload_resume_links.py`,
  `site_patterns.json`. The env var for the private-dir override is `PRIYA_PRIVATE_DIR` (not
  `MANJU_PRIVATE_DIR`).
- **English-only** — no Finnish-language tailoring path exists for Priya (unlike Manju's
  `master_data_fi.json` branch), since her own `job_requirements.md` already hard-rejects any job
  requiring Finnish/Swedish, so a Finnish resume would never actually be used. This meaningfully
  simplified `tailor-resume.md` versus Manju's version — no language-detection branch, no
  `resume.labels`/`cover_letter.salutation` handling.
- **Fields deliberately omitted from her `master_data.json`** (vs. Manju's template): no
  `date_of_birth` (not provided — forms that ask for it get left blank, flagged for manual fill,
  never invented), no `wage_subsidy_note` (palkkatuki eligibility unknown — don't assume she
  qualifies), no `achievements`/`publications_html` (none known). `make_resume.py` handles all of
  these as optional/blank gracefully. **`volunteering` is no longer in that omitted list** — see
  the dated entry below (added 2026-09-17).
- **References** (from the user, not the resume itself — neither of her source resumes lists any):
  Vili Lang (Line Manager, Elektrobit) — vili.lang@elektrobit.com; Morgane Fleuriot Pajunen
  (Engineering Manager, Topcon Healthcare) — Mob: 0504840007; Riitta Kasoli (QARA Specialist,
  Topcon Healthcare) — riitta.kasoli@topcon.com.
- **Employment status corrected**: her Topcon role actually ended **July 2026** (not "–Present" as
  it might read out of context from the resume's own date range) — she is currently unemployed and
  immediately available. `job_requirements.md`'s Candidate Profile was updated to reflect this.
- **Photo**: `priya_photo.jpg`, provided directly by the user in a Claude Code session and saved to
  `Priya_jobs_private\priya_photo.jpg`.
- **Chrome automation profile**: named `Priya Automation Profile` (not `Automation Profile`) in
  `fill-form.md`, deliberately distinct so it never collides with any other candidate's saved
  logins if ever tested on a shared machine — though in practice this runs on Priya's own PC where
  no collision risk exists anyway.
- `Priyanga_CV_ICEYE_TechReleaseManager.pdf` (in `originial resumes\`) remains the tailoring
  *style* reference noted earlier — shows how she's already reframed her experience toward a
  release-management-titled role, useful context for `tailor-resume.md`'s profile-writing step.

## Volunteering entry added to every resume (2026-09-17)

Priya's `master_data.json` now has a real `resume.volunteering` entry (previously omitted as "none
known" — see the note above): Namaste Oulu, a cultural program she successfully conducted as part
of Oulu's European Capital of Culture (Oulu2026). Per her instruction, this must appear in **every**
tailored resume, not selectively. Wired into `tailor-resume.md`'s Step 2 (new
`resume.volunteering` rule: always copy verbatim from the template, never omit) and into the
master template itself, so future `/tailor-resume` runs pick it up automatically. Retrofitted into
all resumes tailored before this date (`68d00df0`, `4b53885e`, `5561f02a`, `a5ad27d8`,
`69260c28`, `83b81ba0`, and the manually-added Nokia job `00035459` — see below) by editing each
job's `*_data.json`, regenerating HTML/PDF, and re-verifying the rendered PDF. Since the PDF file
paths/URLs didn't change, no Firestore/`input.csv` re-sync was needed — the dashboard's existing
links already serve the updated file content.

## Manually-tailored jobs outside the scraped pipeline (2026-09-17)

Not every `/tailor-resume` target comes from `jobs.json` — Priya can paste a job posting she found
herself (e.g. copied directly from a company's careers site when the URL itself isn't fetchable,
like an Oracle Cloud HCM / Taleo-style JS-rendered career page that returns nothing useful to
WebFetch or web search). For these: generate a synthetic 8-hex-char `job_id` (the company's own
posting ID, zero-padded, works well and stays traceable — e.g. Nokia posting 35459 became
`00035459`), write `PRIVATE\Resumes\<job_id>\<job_id>_data.json` and generate the PDFs exactly as
normal, and commit to `PRIVATE` — but **skip** `sync_resume_links.py` and the `PUBLIC\input.csv`
commit (Steps 6–7), since a job with no `jobs.json` entry would just log a
`WARN: job_id '...' not found in jobs.json — skipping` and never actually reach Firestore/the
dashboard anyway. These stay as local-only PDFs for Priya to use directly, not dashboard-tracked
jobs. No checkpoint file is created either, since the checkpoint/resume mechanism assumes a
`jobs.json`-backed job. **Caveat:** `sync_resume_links.py` rescans *every* `Resumes\` folder, so
the next sync run for any other job still logs that WARN for the manual job and writes its row
into `input.csv` (row for `00035459` was committed 2026-09-21 this way). Harmless — no Firestore
write, and the CSV links point at the private repo — but don't expect skipping Steps 6–7 to keep a
manual job out of `input.csv` permanently.

## Work-permit status + started-learning-Finnish must appear in every cover letter (2026-09-16)

Two standing candidate-profile facts, both added the same day by explicit user instruction, both
wired the same way — into `tailor-resume.md`'s Step 2 (`cover_letter.paragraphs` point 4 + the
"Priya's profile" facts list) and into `priya-jobs-private/Resumes/Master/master_data.json`'s
reference cover-letter paragraph — so future `/tailor-resume` runs pick both up automatically
without being told again. Both retrofitted into the already-generated `68d00df0` (memberio) cover
letter (regenerated PDF each time, visually re-verified before recommitting):
- Priya holds a valid residence permit to work in Finland (no visa sponsorship needed) and has
  already applied for a permanent residence permit there (in progress, not yet granted). The old
  "previously held valid work authorization through prior employment" phrasing in
  `tailor-resume.md` was stale/imprecise and has been replaced with this.
- She has started learning Finnish, "with a view to the future" — phrased strictly as a
  forward-looking commitment, never as current fluency/conversational ability. **Does not change**
  the existing fact that she does not currently speak Finnish/Swedish and `job_requirements.md`
  still hard-rejects roles requiring either — this is a distinct, compatible fact, not a walk-back
  of that rule.

## Cross-machine setup on Priya's own PC completed (2026-09-15)

Done on Priya's actual machine (`C:\Users\priya`), not this session's original dev box. Notes for
future sessions:
- Neither `git` nor real `python` was present — Windows' bare `python`/`python3` resolved to the
  Microsoft Store stub alias, not an interpreter. Installed both via `winget` (`Git.Git`,
  `Python.Python.3.12`), plus `GitHub.cli` since neither repo's `git clone` worked unauthenticated
  (the *public* dashboard repo also demanded credentials over plain HTTPS with no stored
  credential helper — not just the private one). `gh auth login` (interactive browser flow, done
  by Priya herself) plus `git config --global credential.https://github.com.helper "!gh auth
  git-credential"` unblocked both clones.
- **`vinchess1989/priya-jobs-private` did not exist yet at the start of this session** — `gh repo
  view`/`gh repo list vinchess1989` confirmed no such repo, not a permissions issue. It came into
  existence (with `Resumes/` and `priya_photo.jpg` already in it) between one retry and the next,
  presumably created by the account owner outside this session. Confirmed `PRIVATE` visibility via
  `gh repo view ... --json visibility` after cloning.
- Cloned as true siblings: `C:\Users\priya\priya_jobs\` and `C:\Users\priya\priya-jobs-private\`
  (this time using the hyphenated name matching the GitHub slug, unlike the earlier
  `Priya_jobs_private` local-folder naming on the other machine noted below — both work fine since
  `find_repos.py` matches on git remote URL, not folder name).
- **Gotcha that cost real debugging time**: this environment's shell tool spawns a fresh process
  per command with no PATH refresh after `winget install`, so `git`/`gh`/`python` all resolved to
  "not recognized" even right after a successful install — and, non-obviously, this also silently
  broke `find_repos.py` itself on the first attempt (`PUBLIC_REPO=NOT FOUND` /
  `PRIVATE_REPO=NOT FOUND` even though both repos were correctly cloned right there), because its
  `subprocess.run(["git", "-C", ...])` calls inherit the same stale-PATH process environment as
  whatever invoked it — a `NOT FOUND` result from this script doesn't necessarily mean a sibling
  layout problem, check `git` is actually on PATH in that same shell first. Fixed per-command by
  rebuilding `$env:Path` from the Machine+User registry values before every git/python invocation.
- venv + `pip install playwright requests python-dotenv beautifulsoup4 filelock pytest` +
  `playwright install chromium` all succeeded normally once PATH/git/python were sorted.
  `find_repos.py` and `job_status_store.py get --url ... --field apply_url` (→ `NONE`) both verified
  working. Chrome already present at the standard path. `setup_windows_scheduler.bat`
  deliberately not run yet, per instruction, pending Priya's own end-to-end
  `/tailor-resume`/`/fill-form` test.

## Reasoning-model reviewer silently rejected everything (found + fixed 2026-09-20)

Symptom: dashboard "no jobs added" for days — total plateaued ~4,400 and Yes count froze at ~342-345.
Real cause: LM Studio's active local model was switched to `google/gemma-4-26b-a4b-qat`, a **reasoning
model**. With `max_tokens=500` it spent ~497 tokens on hidden reasoning (`reasoning_content`), returned
**empty `content`** (`finish_reason: "length"`), `extract_json_from_text` fell to its regex fallback (reason
"Extracted via regex fallback"), and `review_pending_jobs` then coerced the unparseable result to **"no"**.
Result: 1,449 gemma reviews → 0 yes / 0 maybe / 1,449 "no", plus ~250 more from Groq qwen/gpt-oss (same
truncation). Genuine on-domain jobs (Release Manager, Senior DevOps Engineer, ANYbotics DevOps Remote…)
were buried as "no". `manju_jobs` got hit identically (~2,050 gemma reviews, 100% fallback); `vineeth_jobs`
barely used gemma (8 reviews) so it's mostly fine.

Fixes in `scraper.py`: (1) `_truncated_without_verdict()` + a one-time retry with `max(max_tokens*8, 4096)`
in both `_try_cloud_provider` and the local branch of `_call_llm_with_fallback` (gemma needs ~1,100 tokens;
LM Studio ignores `reasoning_effort` / `enable_thinking` for it — verified, so budget is the only lever);
(2) unparseable/missing verdict is now `"error"` (retried next cycle) instead of coerced to `"no"`;
(3) the review prompt no longer says "semiconductor/VLSI/EDA reviewer" (fork leftover) or lists Indian-city
location examples. 217 wrongly-"no" jobs with on-domain titles were re-queued via `needs_re_review`.
Other diagnostics worth knowing: "Extracted via regex fallback" as a job's `reason` is the fingerprint of
this failure — `Where reason -like "*regex fallback*"` counts affected jobs; `jobs_history.json` daily
`NetAdded` + per-`eval_model` verdict counts in `jobs.json` are the fastest health check. Gemma at 4096
tokens is slow (~1-2 min/job under LM Studio contention); `hermes-3-llama-3.1-8b` (non-reasoning) had 0%
fallback and is ~10x faster if a fast local model is preferred — the LM Studio model is shared by all three
dashboards, so switching it is the user's call. Groq `qwen/qwen3.6-27b` now returns 404 (model retired) —
removed from priya's `GROQ_MODELS` default on 2026-09-28; manju/vineeth defaults still list it.

Why gemma-4-26b is slow (checked 2026-09-27): its LM Studio load config
(`~\.lmstudio\.internal\user-concrete-model-default-config\google\gemma-4-26b-a4b-qat.json`) has
`offloadRatio: 1`, so the 15.6 GB Q4_0 file is forced onto the 12 GB RTX 3060 and spills into Windows shared
memory (nvidia-smi 11.5/12 GB used). **Fixed 2026-09-27:** added `{"key":
"llm.load.numCpuExpertLayersRatio", "value": 1}` to that file's `load.fields` (LM Studio's "Force Model Expert
Weights onto CPU"; key found in the app bundle, not exposed by `lms load`). VRAM now ~5 GB and generation
went 7.5 → 18.5 tok/s (median 104 s/job, was ~174). Original file backed up in that session's scratchpad only.
The custom chat template defaults `enable_thinking` to false, but the QAT build **still emits
`reasoning_content`**, and LM Studio also ignores `chat_template_kwargs.enable_thinking=false` for qwen3-14b,
so for local reasoning models thinking can't be turned off; keep the 4096 budget.

**Local model benchmark (2026-09-27, 30 jobs: 10 yes / 8 maybe / 12 no by cloud verdict, scraper's exact
prompt, pipeline lock held):**

| model | s/job | exact = gemma ref | close (yes≈maybe) = cloud | cloud yes/maybe → "no" | errors |
|---|---|---|---|---|---|
| gemma-4-26b (experts on CPU) | 104 | ref | 18/25 | 6 | **5/30 used all 4096 tokens reasoning, no verdict** |
| qwen/qwen3-14b | 23 | 18/25 | 21/30 | 9 | 0 |
| gemma-3-12b-it | 9 | 14/25 | 21/30 | 4 | 0 (2 cloud-"no" → yes) |
| ibm/granite-4.1-8b | 6 | 17/25 | 19/30 | 5 | 0 (2 cloud-"no" → yes) |
| openai/gpt-oss-20b (effort low) | 15 | 17/23 | 17/28 | 10 | 0 |
| hermes-3-llama-3.1-8b | 6 | 15/25 | 14/30 | **15** | 0 |
| qwen/qwen3.6-35b-a3b (60% experts on CPU, run isolated) | 79 | **23/25** | 20/30 | 8 | 0 |

Hermes rejects 9/10 cloud-"yes" jobs, so the earlier "0% fallback, 10x faster" note above hides that it
mostly says no. Qwen3-14B is the best fit: 23 s/job, fully on the GPU (8.4 GB), and agreement with the cloud
verdicts as good as or better than every bigger model. Qwen3.6-35B-A3B (`numCpuExpertLayersRatio` 0.6 in its
LM Studio config; 11 GB VRAM + ~12 GB RAM, 38.7 tok/s but ~2,280 reasoning tokens/job) almost duplicates
gemma-4-26b's verdicts without gemma's 17% out-of-budget failures and is 25% faster. It's a drop-in gemma
replacement, but not more accurate than Qwen3-14B against the cloud verdicts, and it's 3.4x slower.

**Switched 2026-09-28:** the local model is now `qwen/qwen3-14b` (32k context, parallel 1, q8_0 KV cache,
fully on the GPU) for all scrapers (manju, priya, priya_global; vineeth's task is disabled) and as OpenClaw's
primary (`openclaw.json` primary `openai/qwen/qwen3-14b`, contextWindow 32768). OpenClaw needs more than
25k context, so never load the shared model below 32k. The details live in
`~\.claude\local_llm_models.json`. The retired Groq `qwen/qwen3.6-27b` was dropped from priya's `GROQ_MODELS` the same day.
**Benchmark gotcha:** each scraper runs `lms ps` at the start of a review batch and keeps requesting that
model. Swapping models mid-benchmark made a scraper JIT-reload qwen3-14b next to gpt-oss (experts in RAM),
which used all 32 GB of RAM and hung the LM Studio server until the gpt-oss worker was killed. Don't load two
big models at once. Reload with `--identifier google/gemma-4-26b-a4b-qat`, or LM Studio names the instance
`...:2` and a scraper saves that suffix in `checkpoint.json`.

**Who else loads models (found 2026-09-27):** OpenClaw hard-codes gemma-4-26b as its primary model in
`~\.openclaw\openclaw.json` and calls both `/v1/chat/completions` and `/v1/responses`, so any OpenClaw
activity JIT-loads gemma next to whatever is loaded. It ignores the pipeline lock. The scrapers fall back to
`checkpoint.json`'s `last_llm_model` whenever `lms ps` is empty or times out, which happens when the server is
stalled or right after a reboot. For an isolated model test, stop the `OpenClaw Gateway` task and kill its
`openclaw\dist\index.js` node process, then restart it afterwards; holding the lock alone is not enough.
The first qwen3.6-35b-a3b attempt stalled the server and the PC rebooted at 20:02.
**Scraper tasks don't restart after a reboot:** `ManjuJobsLocalLLMOrchestrator` and
`PriyaJobsLocalLLMOrchestrator` have only daily triggers (00:00 / 08:00), so after a reboot they stay stopped
until the next trigger (last result 0xC000013A = killed by the shutdown). OpenClaw has a logon trigger.
**RAM (32 GB) is the real ceiling:** gemma/qwen3.6 with experts on CPU take 12–14 GB of RAM. Antigravity IDE
leaked 71 `ms-playwright-go\1.57.0\node.exe` drivers (~4.7 GB) between 23 and 27 Sep; the reboot cleared them.

## Open/unresolved

- Priya still needs to do the first manual sign-in to whatever job sites she'll apply through, in
  the `Priya Automation Profile` Chrome window that `fill-form` launches on first use — never
  automated, never done with her credentials by any session. Hasn't happened yet as of this setup.
- **No Node.js/npm is needed**
  — `fill-form.md`'s Step 4/5 browser automation (and `open_visible_browser/SKILL.md`) are 100%
  Python `playwright.sync_api`, already covered by the venv above. (Earlier draft of this note
  incorrectly said Node.js/`npm install playwright-core` was required, confusing this with the
  unrelated Node-based `chrome-automation` tooling this Claude session itself used for one-off
  browser automation during setup — that tooling has nothing to do with what the ported skills
  actually run.)
- Verified end-to-end on this machine (2026-09-09): generated a real resume PDF from Priya's actual
  `master_data.json` and real photo via `make_resume.py` + `html_to_pdf.py`, visually confirmed
  correct rendering. Not yet run via the actual `/tailor-resume` skill invocation against a real
  job ID from `jobs.json` (only the underlying scripts were tested directly) — worth doing once,
  either here or from Priya's machine, to confirm the full skill flow end-to-end.
- `add-job-link.md`, `test-and-publish.md`, and the `email-apply` skill are still not ported — none
  are dependencies of the four skills above; separate work if wanted later.
- Full authenticated dashboard testing (a write action like "mark applied", signed in as
  `priyabkc99@gmail.com`) still needs Priya to do it herself — not something to automate via
  browser automation with her credentials.

## Re-Review Button Removal & Request Neutralization (2026-09-26)

- **Inadvertent Click Ignored**: The user accidentally clicked the dashboard's "Re-Review" button. `shared_state/re_review_request` in Firestore was immediately patched from `status: "requested"` to `status: "idle"` before the scraper's `poll_re_review_request()` picked it up, preventing an unintended batch re-evaluation loop of all jobs.
- **Button Removal from Dashboard**:
  - Removed `<button id="re-review-btn">` and its parent container from [firebase_app/index.html](file:///c:/Users/vinee/priya_jobs/firebase_app/index.html).
  - Cleaned up `#re-review-btn` CSS rules, mobile responsive styling, and `:has(> #re-review-btn)` layout queries.
  - Removed `triggerReReview()` and the real-time `onSnapshot` listener on `shared_state/re_review_request`.
- **Deployment**: Successfully deployed the updated dashboard to Firebase Hosting (`priya-jobs-dashboard.web.app`).

## Keywords & Role Expansion Audit (2026-09-26)
- Recent additions to [job_requirements.md](file:///c:/Users/vinee/priya_jobs/job_requirements.md) (`Quality Engineer / Quality Manager`, `Requirements Engineer`, `EU MDR / ISO 13485`, `Technical Writer / Documentation Specialist`, `Test Engineer`) yielded **75+ YES matches** and multiple MAYBEs, including high-fit roles in Oulu (NestAI QA Full-Stack, Nordea Backend QA, Nokia Defense Test Engineer) and across Finland (Teleste Quality Manager, Eaton Customer Quality Engineer, ICEYE Supplier Quality Engineer).

---
Last updated: 2026-09-26

## Error-retry cap (2026-09-29)
Jobs whose review fails (`matches_requirements: "error"`: page won't load, unparseable LLM output) are
now retried at most 3 times, at least 6 h apart (`_needs_review` / `_record_review_outcome`,
`ERROR_MAX_ATTEMPTS` / `ERROR_RETRY_SECONDS`; per-job `error_attempts` / `last_error_at`, cleared by any
real verdict). Previously every loop retried them. On priya_global_jobs, 5 always-failing Totaljobs pages
turned that into a retry+commit+push every ~9 s (258 commits/hour). The error cap is checked before
`needs_re_review`, so a failing re-review job can't loop either. Capped jobs stay "error" on the
dashboard. To force a retry, delete those two fields from the job.
## Mobile bottom nav: filter indicator (2026-09-29)
While any filter is set (column filters, date limits, location text, LLM pills, and on the global board countries), the Filters tab shows an amber dot and its label becomes `shown/total` (e.g. 32/222), set at the end of `filterTable()` (`#mnav-filters.has-filters`). Total = rows in the table, i.e. excluding archived 'no' jobs unless shown. Also 2026-09-29: the header board link is now a prominent `.board-switch` button (icon-only on phones) on both Priya boards.

## Firestore lockdown rules actually deployed 2026-09-29
The 2026-09-28 lockdown entry above said bare calls 'now get 403', but the locked-down `firestore.rules` had never been deployed: anonymous reads still returned 200 on all four projects (priya-jobs-dashboard, priya-global-jobs, manju-jobs-dashboard, vineeth-jobs-dashboard) until 2026-09-29, when they were deployed with `firebase deploy --only firestore:rules`. Verified after deploy: anonymous GET on `shared_state/job_status` and `user_feedback` -> 403; service-account `firestore_auth.session()` -> 200; no unauthenticated Firestore calls in any repo's .py files. **To check the lockdown, test anonymous access yourself** (`Invoke-WebRequest https://firestore.googleapis.com/v1/projects/<id>/databases/(default)/documents/shared_state/job_status` should throw 403). The committed rules file alone proves nothing. Any other machine needs its `~/.secrets/<project>-sa.json` key (Manju's PC was pending at deploy time).

## Scraper was wiping shared_state/job_status (found + removed 2026-09-30)
`poll_firebase_feedback()` ended with `PATCH shared_state/job_status {"fields": {}}` ("clear the temporary queue")
whenever dashboard feedback produced user_review/match updates. job_status is a PERMANENT per-job store (resume
links, apply_url/apply_email, applied_date, form_filled, action_item, tailor_model, deletion_reason), so every such
run erased it for every job. priya_jobs logs show it ran 17 times (latest 2026-09-29); the doc was found empty on
2026-09-30. Removed from priya_jobs, priya_global_jobs and vineeth_jobs (manju_jobs no longer had it). Recovery:
resume/cover-letter links rebuilt with `sync_resume_links.py --upload --force` (20 jobs). NOT recoverable:
apply_url/apply_email, applied_date, form_filled, action_item, auto_fill_attempted_at (Firestore PITR is off).
applied / user_review / matches values survived because they had already been synced into jobs.json.
**Never write an empty/whole replacement document to job_status** — read-modify-write only (job_status_store.py).
## Manual deletions now processed (2026-09-30)
`poll_manual_deletions()` (ported from manju_jobs) runs every main-loop pass: jobs whose Firestore job_status entry
has `deletion_reason` (mark-job-deleted skill, job_status_store.py, review.html "missed" button) are moved from
jobs.json to deleted.json with that reason, then the flag is cleared (other fields kept). Before this, no Priya
scraper read the flag, so such jobs stayed on the main board. Tested on temp copies with a simulated job_status.