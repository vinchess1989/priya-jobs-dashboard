# Project Memory — priya_jobs

Durable, cross-session knowledge specific to this project. Set up 2026-09-06 as a third
job-finder automation alongside `manju_jobs` (Finnish generalist roles) and `vineeth_jobs`
(global VLSI/semiconductor roles) — see [../manju_jobs/memory.md](../manju_jobs/memory.md) and
[../vineeth_jobs/memory.md](../vineeth_jobs/memory.md) for the shared infrastructure this project
plugs into.

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
drop it from `GROQ_MODELS`.

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

---
Last updated: 2026-09-17
