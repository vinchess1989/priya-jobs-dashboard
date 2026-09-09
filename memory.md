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
  qualifies), no `volunteering`/`achievements`/`publications_html` (none known). `make_resume.py`
  handles all of these as optional/blank gracefully.
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

## Open/unresolved

- **Cross-machine setup on Priya's own PC not yet done** (this session can't touch her physical
  machine): clone both repos (needs her own GitHub collaborator access on
  `vinchess1989/priya-jobs-dashboard` and `vinchess1989/priya-jobs-private`), Python venv +
  `pip install` + `playwright install chromium` (the pip package alone doesn't include the browser
  binary), Chrome installed, and a first manual sign-in to whatever job sites she'll apply through
  in the `Priya Automation Profile` browser window (never automated). **No Node.js/npm is needed**
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
Last updated: 2026-09-09
