Open a single job's application form in a visible, already-logged-in browser and fill it using your own judgment — ensures a Claude-tailored resume/cover letter exist first, resolves the real form URL via find-apply-link, then pauses before submit so Priya can review and click submit herself. With no job ID given, instead runs the full discovery flow first (find near-deadline unapplied jobs → verify liveness → flag expired ones for deletion → build a reasoned checklist → let Priya pick) and then applies the single-job flow to whatever she picks.

The arguments are: **$ARGUMENTS**

Parse `$ARGUMENTS` by space-separated tokens:
- Any token matching `^[0-9a-fA-F]{8}$` (case-insensitive) is treated as an explicit `JOB_ID`.
- Any token matching `^post<(\d+)$` or `^posted<(\d+)$` (case-insensitive) extracts `POSTED_DAYS_LIMIT` (e.g. `post<3` means jobs posted in the last 3 days, i.e., `posted_date >= today - 3 days`).
- Any token matching `^dead<(\d+)$` or `^deadline<(\d+)$` (case-insensitive) extracts `DEADLINE_DAYS_LIMIT` (e.g. `dead<3` means jobs expiring in the next 3 days, i.e., `today <= deadline <= today + 3 days`).
- Any token matching `^auto$` (case-insensitive) sets `$AutoMode = $true` — unattended mode, meant for a recurring `/loop 60m /fill-form auto`. Picks a job itself instead of asking, and never blocks on user input anywhere in the run.

**If no explicit `JOB_ID` token is given**:
- `$AutoMode = $true` → run Step -1.A (auto-pick) instead of Step -1, then fall through to Steps 0–6 for whatever it picks (if anything — some cycles legitimately pick nothing).
- Otherwise → run Step -1 (discovery mode) using the parsed `POSTED_DAYS_LIMIT`/`DEADLINE_DAYS_LIMIT` filters, then come back and run Steps 0–6 for each job selected there.

Examples:
- `/fill-form 1ee84312` — process single explicit job ID
- `/fill-form post<3 dead<3` — discover unapplied jobs posted in the last 3 days AND expiring within the next 3 days
- `/fill-form dead<5` — discover unapplied jobs expiring within the next 5 days
- `/fill-form` — discover unapplied jobs expiring today or tomorrow (default)
- `/fill-form auto` — unattended: auto-pick one strong-match job, fill it, stop before submit; never blocks waiting for input (meant to run every hour via `/loop 60m /fill-form auto`)

---

## Constants (resolved at runtime — device-agnostic)

**`PUBLIC`** — repo root. `$PUBLIC = (Get-Location).Path`

**`PRIVATE`** — the private companion repo. Resolve in this order and stop at the first hit:
1. The environment variable `PRIYA_PRIVATE_DIR` if set.
2. A sibling of PUBLIC whose name contains "private" (case-insensitive):
   ```powershell
   $parent  = Split-Path $PUBLIC -Parent
   $PRIVATE = Get-ChildItem $parent -Directory |
              Where-Object { $_.Name -match 'private' } |
              Select-Object -First 1 -ExpandProperty FullName
   ```
3. A sibling of PUBLIC that contains a `Resumes\` subfolder:
   ```powershell
   $PRIVATE = Get-ChildItem $parent -Directory |
              Where-Object { Test-Path "$($_.FullName)\Resumes" } |
              Select-Object -First 1 -ExpandProperty FullName
   ```
4. If still not found — stop and ask the user to set `PRIYA_PRIVATE_DIR`, then re-run.

**`JOBS_JSON`** = `PUBLIC\jobs.json` — read-only lookup only.

**`AUTOMATION_PROFILE`** = `$env:LOCALAPPDATA\Google\Chrome\Priya Automation Profile` — the persistent Chrome profile with Priya's saved logins (LinkedIn, Duunitori, etc.). Always launch against this profile, never a fresh/incognito context, so platforms she's already authenticated on don't hit a login wall. This is a separate profile name from any other person's automation profile that might exist on the same machine, so their saved logins never collide. See `.agents\skills\open_visible_browser\SKILL.md` and the `talent.core.eezy.fi` entry in `site_patterns.json` for the working reference pattern.

---

## Step -1 — Discovery mode (no explicit JOB_ID given, not `$AutoMode`)

Only runs when no explicit `JOB_ID` token is provided **and `$AutoMode` is false**. Find unapplied jobs matching date filters (`post<X` and/or `dead<Y`), verify each is genuinely still open, flag truly expired ones for deletion, build a reasoned fit checklist for what's left, let Priya pick, then fall through to Steps 0–6 for each pick. (When `$AutoMode` is true, Step -1.A runs instead — see below — since this step's human picker at -1.5 has nobody to answer it during an unattended `/loop` tick.)

### -1.0 — Pull latest jobs.json

```powershell
git pull --rebase origin main
```
If `git status --short` shows unrelated pre-existing modified tracked files (e.g. from a concurrent session), stash just those paths first, pull, then pop — never discard someone else's in-progress work.

### -1.1 — Find unapplied jobs matching date filters

Calculate filter dates based on parsed `$ARGUMENTS`:
- **Deadline filter (`dead<Y`)**: If `dead<Y` is given, filter jobs with `deadline` between `$today` and `$today + Y days`. If neither `dead` nor `post` is specified, default to `Y = 1` (today or tomorrow).
- **Posted filter (`post<X`)**: If `post<X` is given, filter jobs with `posted_date` between `$today - X days` and `$today`.

```powershell
$today = (Get-Date).ToString("yyyy-MM-dd")
```
Read `JOBS_JSON`. Candidates are entries satisfying the active `posted_date` and `deadline` filters, **and** where `applied` is not `"yes"`.

For each candidate, also check Firestore's `applied` field (`python job_status_store.py get --url "JOB_URL" --field applied`) — jobs.json's cached `applied` can lag behind what's actually been submitted. Drop any job where **either** source says `"yes"` (jobs already handled don't need re-triage).

If the raw candidate count is large, it's fine to narrow further using `matches_requirements` (prioritize `"yes"`/`"maybe"` over `"no"`) to keep the liveness-check pass manageable — state the excluded count when reporting so nothing silently vanishes.

### -1.2 — Verify each candidate is still genuinely open

For each remaining candidate:
- **tyomarkkinatori.fi** listings: use the cached `api` strategy in `site_patterns.json` (`https://tyomarkkinatori.fi/api/jobposting-new/v1/public/jobpostings/{id}`) — check `metadata.status` (4 = active, 5 = closed) and `application.expires`. Don't rely solely on jobs.json's cached `deadline`: the live `expires` value can be later than the cached one (extended deadline — treat as not urgent, no deletion action needed) or the status can already be 5 before the cached deadline even passes.
- **Other domains**: WebFetch the `url`. Ask a narrow, UI-only question — do **not** ask the model to reason about the deadline date, since that reasoning has proven unreliable elsewhere in this family of skills. Use a prompt along the lines of: *"Reply ACTIVE if there is a live 'Apply' button or open application form on this page. Reply CLOSED if the page explicitly states the position is filled, applications are no longer accepted, or the listing was removed. Do not reason about the deadline date — only report what the page's UI currently shows."*

### -1.3 — Flag genuinely expired/closed jobs for deletion

For each candidate confirmed CLOSED in -1.2, apply the **mark-job-deleted** skill technique (`.claude/commands/mark-job-deleted.md`), including its **applied guard**: if either jobs.json or Firestore already says `applied == "yes"` for that job, do not auto-delete — report it and ask the user first (leaving it alone is a valid outcome — a completed application shouldn't disappear from the dashboard just because the listing expired). Otherwise set `deletion_reason` in Firestore directly with no confirmation needed.

### -1.4 — Build a reasoned checklist for what's left

For every candidate confirmed ACTIVE, read its `description_file` (accept it if it has more than 200 meaningful words after the `JOB DESCRIPTION:` header; otherwise WebFetch the job's `url`, or search `"JOB_TITLE" "COMPANY" job"` as a last resort) and write one row of honest fit reasoning per job: title, company, location, deadline, and an assessment against Priya's actual profile and `job_requirements.md` criteria (Oulu-based, 15 years Configuration Management/Release Management/DevOps background, targeting DevOps Engineer / Configuration Manager / Release Manager / Product Specialist roles, Finland + remote-EU scope, English-only — no Finnish/Swedish, not currently employed so immediately available). Call out location mismatches (especially any US-based role — strictly excluded, see `job_requirements.md`'s hard rejections), hard-requirement gaps (e.g. mandatory Finnish/Swedish, entry-level-only programs, pure feature-development roles with no release/CM/DevOps component), and role-type issues (e.g. quota-carrying sales roles mislabeled as "Product Specialist") as plainly as genuine strengths — don't inflate weak matches to pad the list. Present this as a table.

### -1.5 — Let Priya select

Ask which job ID(s) to proceed with — a plain-text question is fine (a checklist can easily run past `AskUserQuestion`'s 4-option cap). While waiting on her answer, for every checklisted job that is ultimately **not** selected, set `matches_requirements = "no"` and `user_reason = <the fit assessment written in -1.4>` in Firestore via `job_status_store.py`. This is what the live dashboard reads as an override on top of jobs.json's own `matches_requirements`/`reason` fields, so it corrects the scraper's classification for anyone reviewing the dashboard later — without ever touching `jobs.json` itself.

**Also feed genuinely new patterns into `job_requirements.md`** so the scraper's own AI matching improves over time instead of repeating the same false-positive next run. Apply the **update_requirements_vineeth** skill technique (`.agents\skills\update_requirements_vineeth\SKILL.md`), using the fit-assessment reasons just written in -1.4 for every non-selected job as the feedback input. For each rejection reason, judge whether it's a *repeatable pattern* not already covered by an existing `## Hard Rejections` / `## Target Job Criteria` bullet in `job_requirements.md`, and skip anything already handled (e.g. "based in the US" is already covered by the existing hard rejection). Where a genuinely new pattern emerges, surgically edit the appropriate existing section — don't append raw reason strings at the bottom, and don't add a rule for a one-off that isn't likely to recur. If `job_requirements.md` changed, commit and push it to PUBLIC (pull-rebase first, same as any other PUBLIC commit this skill makes).

### -1.6 — Apply Steps 0–6 to each selected job

For each `JOB_ID` Priya selects, run Steps 0 through 6 below exactly as if that ID had been passed as `$ARGUMENTS`.

---

## Step -1.A — Auto-pick (unattended, `$AutoMode` only)

Only runs when `$AutoMode` is true. Meant to be invoked on a timer (`/loop 60m /fill-form auto`) with nobody necessarily watching, so nothing here may block on human input — anywhere Step -1/-1.5 would normally ask a question, this instead logs an action item or just moves to the next candidate.

### -1.A.0 — Pull latest jobs.json

```powershell
git pull --rebase origin main
```
Same stash-first caveat as -1.0 if `git status --short` shows unrelated pre-existing modifications from a concurrent session.

### -1.A.1 — Chrome/CDP pre-flight

Confirm a working browser is actually reachable *before* touching any job's Firestore state — cheaper to find out now than after tailoring/scraping a candidate that can never reach Step 4 anyway:

```powershell
$port_open = Test-NetConnection -ComputerName 127.0.0.1 -Port 9222 -WarningAction SilentlyContinue
if (-not $port_open.TcpTestSucceeded) {
    # Same schtasks launch as Step 4
    Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue
    taskkill /F /IM chrome.exe /T
    $batPath = "PUBLIC\scratch\launch_cdp_chrome.bat"
    Set-Content -Path $batPath -Value "@echo off`n`"C:\Program Files\Google\Chrome\Application\chrome.exe`" --remote-debugging-port=9222 --user-data-dir=`"$env:LOCALAPPDATA\Google\Chrome\Priya Automation Profile`""
    cmd /c "schtasks /delete /tn `"PriyaVisibleBrowser`" /f & schtasks /create /tn `"PriyaVisibleBrowser`" /tr `"\`"$batPath\`"`" /sc once /st 00:00 /it /f & schtasks /run /tn `"PriyaVisibleBrowser`""
    Start-Sleep -Seconds 4
    $port_open = Test-NetConnection -ComputerName 127.0.0.1 -Port 9222 -WarningAction SilentlyContinue
}
if (-not $port_open.TcpTestSucceeded) {
    Write-Host "Chrome/CDP unavailable this cycle (PC likely locked?) — skipping. Nothing marked attempted."
    exit
}
```
This is the known limitation of automating via `/loop` in a session Priya keeps open rather than a true background service: if the PC is locked when a tick fires, that hour is a clean no-op instead of a hang or a false failure — accepted, not something to solve here.

### -1.A.2 — Build the candidate list

From `JOBS_JSON`, a candidate satisfies **all** of:
- `matches_requirements == "yes"` (Firestore's `shared_state/job_status` override wins over jobs.json's own field, same precedence the dashboard already uses — this now also includes weak matches Priya promoted via the `firebase_app/review.html` Apply button, since that button just sets this same field).
- `applied != "yes"` (check both jobs.json **and** Firestore — jobs.json's cached value can lag behind reality).
- Firestore's `deletion_reason` is not set.
- Firestore's `action_item` is either absent or has `status == "done"` (a `"pending"` action item means this job is a known non-fillable case already on the checklist — don't re-surface it here).
- Firestore's `auto_fill_attempted_at` is not set (a form already filled and staged for review is mid-flight, not idle — never reconsider it).

### -1.A.3 — Sort and batch

Sort the full eligible list with **priority jobs first**, then by `deadline` ascending: any candidate with Firestore's `priority_fill_form_at` set (Priya checked "Done" on a `create_login` action item in `firebase_app/review.html`, signaling the login/account blocker is now resolved and this job should be retried before anything else) sorts to the very front, most-recently-flagged first among those. Everything else follows, sorted by `deadline` ascending: parse `yyyy-MM-dd` entries and sort those first (soonest first); `"Open until filled"`, `"N/A"`, or anything unparseable sorts after all dated entries.

Process in batches of 15 in that order: run -1.A.4 on batch 1 (candidates 1–15); only if the *entire* batch produces zero filled forms, move to batch 2 (16–30), and so on until either a form gets filled or the whole list is exhausted. This bounds each cycle's scrape/tailor cost without giving up early just because the first 15 all happened to be action-item cases.

### -1.A.4 — Walk the batch

**First, check whether Priya already reviewed this one herself.** If Firestore's `user_review == "done"` for this job, she personally clicked "Apply" on it from the Weak Matches tab in `firebase_app/review.html` (that button is the only thing that sets `user_review`) — that's a deliberate human decision, not the scraper's own guess. **Skip the sanity-check below entirely, with no exceptions, and go straight to "Looks fine"** — never re-litigate or demote a job she already explicitly chose to apply to, and never set `matches_requirements` back to `"maybe"` for it. Her Apply click is the final word on fit; this skill's only job from here is Steps 0–3 (tailor docs, resolve the apply URL/email, fill or hand off) — not a second opinion on whether to apply.

Otherwise, for each candidate in order, **sanity-check it first** — cheap, no API cost beyond your own judgment, and worth doing before Step 1 spends real tailoring calls on it. Read the candidate's `title`, `location`, and `reason` (the scraper's own justification) straight from `JOBS_JSON` and briefly judge, against `job_requirements.md`'s own criteria, whether this still looks like a genuine match:
- Does `reason` actually describe *this* job, or does it read like a mismatch?
- Does the title fall into an explicit `## Hard Rejections` category the scraper's LLM may have missed (any US-based role including US remote, mandatory Finnish/Swedish, pure feature-development software engineering, quota-carrying sales mislabeled as "Product Specialist", clinical/medical license required, entry-level-only programs)?
- Is `location` actually consistent with the `## Target Job Criteria` location rule — anywhere in Finland (any work model), or remote/EU-based, is a full `"yes"`. A US location or US-restricted remote role is always a hard "no" — check this first, before role-type fit.

This is a quick plausibility read, not a full re-run of the scraper's evaluation — when genuinely unsure, treat it as passing and continue.

- **Looks wrong** → don't tailor or fill anything for it. Demote it so Priya can decide on her own time instead of this unattended run either silently skipping it or blocking on her answer:
  ```powershell
  python job_status_store.py set --url "JOB_URL" --field matches_requirements --value "maybe"
  python job_status_store.py set --url "JOB_URL" --field user_reason --value "Auto-pick flagged as a likely mismatch: <your one-line reason>"
  ```
  This surfaces it in `firebase_app/review.html`'s Weak Matches tab (Apply/Delete) and removes it from the `"yes"` auto-fill queue, so -1.A.2 won't re-offer it next cycle regardless of what Priya eventually decides. **Never pause this loop waiting for her decision** — move straight on to the next candidate.
- **Looks fine** → run Steps 0–3 below with `$AutoMode` threaded through (Step 3's `$AutoMode` branch passes `--non-interactive` to `scrape_application.py` and does all the outcome branching — email/login-wall/video/normal-form). Step 3's branch itself decides whether to continue to the next candidate or fall through to Steps 4–6.

Keep walking candidates until one falls through to a successful Step 6 hand-off (this cycle's job — stop) or the batch/list is exhausted.

### -1.A.5 — If nothing was fillable

If the whole candidate list is exhausted with no form filled, print a summary and stop cleanly:
```
No fillable form found this cycle — N action items logged, M ambiguous failures, 0 forms filled.
```
This is a legitimate outcome, not an error — some cycles there just isn't a fillable candidate.

---

## Step 0 — Resolve the job

Read `JOBS_JSON`, find the entry with `id == JOB_ID`. If not found, abort with an error rather than guessing.

Record `JOB_TITLE`, `COMPANY`, `JOB_URL`.

---

## Step 1 — Ensure a Claude-tailored resume and cover letter exist

Check whether `PRIVATE\Resumes\JOB_ID\JOB_ID_data.json` exists, whether matching resume/cover-letter PDFs exist alongside it, and whether `data.json`'s `tailor_model` field contains `"claude"` (case-insensitive).

- **All true** → use as-is. Print `Using existing Claude-tailored docs for JOB_ID.`
- **Anything false or missing** → run the **tailor-resume** skill for this job (`.claude/commands/tailor-resume.md`, i.e. `/tailor-resume JOB_ID`) to generate or redo the resume/cover letter, then re-check.

Locate the exact filenames once docs are confirmed:
```powershell
$resumePdf = Get-ChildItem "PRIVATE\Resumes\JOB_ID\*_resume.pdf"        | Select-Object -First 1 -ExpandProperty FullName
$coverPdf  = Get-ChildItem "PRIVATE\Resumes\JOB_ID\*_cover_letter.pdf"  | Select-Object -First 1 -ExpandProperty FullName
```
Abort if either is still missing after tailoring.

---

## Step 2 — Resolve the application form URL

Priority order, stopping at the first hit:

1. **Firestore cache**:
   ```powershell
   python job_status_store.py get --url "JOB_URL" --field apply_email
   python job_status_store.py get --url "JOB_URL" --field apply_url
   ```
   - `apply_email` present (not `NONE`) → email-only, see below.
   - `apply_url` present (not `NONE`) → `APPLY_URL = <that value>`.
2. Otherwise, apply the **find-apply-link** skill technique (`.claude/commands/find-apply-link.md`) using `JOB_URL` as `BASE_URL` and `JOB_ID` as `JOB_ID`. It resolves multi-hop "Apply" chains and JS-rendered links, and self-persists whatever it finds to Firestore (`apply_url`/`apply_email`) so this lookup is instant next time.
   - `RESULT_TYPE: form` → `APPLY_URL = RESULT`.
   - `RESULT_TYPE: email` → email-only, see below.
   - `RESULT_TYPE: not_found` → fall back to `JOB_URL` itself, and warn the user this may just be the listing page rather than the real form.

**Email-only jobs:** if the resolved result is an email address, there is no form to fill. Print `JOB_ID applies via email only (ADDRESS) — no form to fill.`, list the exact `$resumePdf` / `$coverPdf` paths from Step 1 so Priya can attach them herself. (Or use the separate **email-apply** skill, if adapted for this project, to draft the email itself.)

Before stopping, make sure this is on the Action Items checklist (`firebase_app/review.html`) — a fresh `find-apply-link` run already writes this itself (its own Step 5), but a Firestore-cache hit above short-circuits before that ever runs, so backfill it here in that case. Skip if an `action_item` is already recorded `"done"` (don't reopen something already handled):
```powershell
$existing = python job_status_store.py get --url "JOB_URL" --field action_item
$alreadyDone = $false
if ($existing -ne "NONE") { try { $alreadyDone = ((ConvertFrom-Json $existing).status -eq "done") } catch {} }
if (-not $alreadyDone) {
    $actionItem = [ordered]@{
        type = "email_application"; status = "pending"
        created_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        done_at = $null; detail = "Apply via email: ADDRESS"
    } | ConvertTo-Json -Compress
    $tmpFile = "PUBLIC\scratch\action_item_JOB_ID.json"
    Set-Content -Path $tmpFile -Value $actionItem -Encoding utf8 -NoNewline
    python job_status_store.py set --url "JOB_URL" --field action_item --json --value-file $tmpFile
    Remove-Item $tmpFile -ErrorAction SilentlyContinue
}
```
(JSON must go through `--value-file`, never an inline `--value` — PowerShell 5.1 silently strips embedded double-quote characters from native-command arguments, so a raw JSON string never survives on the command line; see `job_status_store.py`'s own docstring.)

In `$AutoMode`, also set `auto_fill_attempted_at` (this job is fully handled for this cycle — no more unattended work is possible on it):
```powershell
python job_status_store.py set --url "JOB_URL" --field auto_fill_attempted_at --value (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
```

**Stop here** — do not continue to Step 3. (In `$AutoMode`, this means: go pick the next candidate per Step -1.A.3, not just stop the whole cycle.)

---

## Step 3 — Get the form's fields and draft answers

1. If `PRIVATE\Resumes\JOB_ID\JOB_ID_answers.json` already exists and its `apply_url` matches `APPLY_URL`, reuse it as-is and skip to Step 4.
2. Otherwise, extract the form's questions:
   ```powershell
   python "PUBLIC\scrape_application.py" --job-url "APPLY_URL" --job-id "JOB_ID" --out-dir "PRIVATE\Resumes\JOB_ID" --private-dir "PRIVATE"
   ```
   In `$AutoMode`, always append `--non-interactive` — without it, a login wall or an unsupported ATS would block forever on an `input()` prompt with nobody there to answer it. The script always writes `PRIVATE\Resumes\JOB_ID\JOB_ID_questions.json` before exiting — even on 0 questions or an expired listing — so read that file for `expired`/`login_wall`/`question_count`/`questions` regardless of its exit code.
   - `question_count > 0` → generate tailored answers and write `JOB_ID_answers.json` + the cheatsheet HTML. **Answer rules:** text/textarea fields get 1–4 sentences (or a full paragraph for open-ended ones), naming the company/role where it fits; select/dropdown fields pick the closest matching option; never invent facts not in Priya's profile. **Known factual fields** — read these from `PRIVATE\Resumes\Master\master_data.json`'s `resume.contact` (or hardcode if faster):
     - Date of birth: not available — if a form requires it, leave blank and flag it as a placeholder for Priya to fill in manually (do not invent a date).
     - Phone / salary expectation: leave blank, mark as a placeholder for manual fill.
     - Address: `Oulu, Finland`.
     - Employment status: `Not currently employed` (her last role, at Topcon Healthcare, ended July 2026).
     - Availability: `Next possible working day`.
     - Willing to relocate: `Yes — open to relocation within Finland, or remote roles based in the EU/Europe` (see `job_requirements.md`'s Location & Work Model criteria for the exact scope).
     - Right to work in Finland: `Yes — has previously held valid work authorization in Finland` (on forms with a structured work-permit dropdown instead of free text, pick the option closest to "I hold/have held a valid work/residence permit").
   - 0 questions / failure (e.g. a login-wall redirect):
     - **Not `$AutoMode`:** no answers file will exist — inspect the live form yourself once the browser is open in Step 5, using WebFetch or a quick throwaway Playwright inspection script against `APPLY_URL` beforehand if you need the field structure before writing the fill script.
     - **`$AutoMode`:** read `expired`/`login_wall` from the questions JSON and branch without asking anyone:
       - `expired == true` → the script already moved this job to `deleted.json` itself. Print a note and go pick the next candidate (Step -1.A.3) — no action item, no `auto_fill_attempted_at` needed, it's gone from `jobs.json`.
       - `login_wall == true` → write a `create_login` action item (pattern below; `detail`: `"Login/account required at <domain of APPLY_URL> — could not get past the login wall automatically."`), set `auto_fill_attempted_at`, go pick the next candidate.
       - neither → ambiguous/unsupported ATS. Print a one-line warning, do **not** set `auto_fill_attempted_at` (cheap enough to just retry next hour rather than invent a fourth action-item type for this), go pick the next candidate.

**`$AutoMode` video-upload check** (only reached when `question_count > 0`, i.e. a form was actually found): scan the questions JSON's `questions` array for any entry with `"type": "file"` where `accept` contains `"video"`, or whose `label` (lowercased) contains one of `video`, `video cv`, `pitch video`, `intro video`. This is a best-effort heuristic, not guaranteed detection — don't over-trust it.
- **Match found** → write an `upload_video` action item (pattern below; `detail`: `"Requires video upload: '<matched field label>'"`), set `auto_fill_attempted_at`, go pick the next candidate — a required video recording isn't something this skill can produce on Priya's behalf.
- **No match** → proceed to Step 4 normally; this candidate is this cycle's job.

**Writing a `create_login` or `upload_video` action item** (`$AutoMode` only — substitute `TYPE`/`DETAIL` per the case above):
```powershell
$actionItem = [ordered]@{
    type = "TYPE"; status = "pending"
    created_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    done_at = $null; detail = "DETAIL"
} | ConvertTo-Json -Compress
$tmpFile = "PUBLIC\scratch\action_item_JOB_ID.json"
Set-Content -Path $tmpFile -Value $actionItem -Encoding utf8 -NoNewline
python job_status_store.py set --url "JOB_URL" --field action_item --json --value-file $tmpFile
Remove-Item $tmpFile -ErrorAction SilentlyContinue
```
(JSON must go through `--value-file`, never an inline `--value` — PowerShell 5.1 silently strips embedded double-quote characters from native-command arguments; see `job_status_store.py`'s own docstring.)

**Marking a job attempted** (`$AutoMode`, every branch above except "expired" and "ambiguous"):
```powershell
python job_status_store.py set --url "JOB_URL" --field auto_fill_attempted_at --value (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
```

---

## Step 4 — Launch Chrome (CDP mode)

We use a detached browser architecture. The automation script connects to Chrome over CDP (port 9222) so it can open new tabs without locking up the browser, allowing multiple forms to be filled sequentially and left open for review.

```powershell
$port_open = Test-NetConnection -ComputerName 127.0.0.1 -Port 9222 -WarningAction SilentlyContinue
if (-not $port_open.TcpTestSucceeded) {
    Write-Host "Launching visible Chrome via CDP..."
    Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue
    taskkill /F /IM chrome.exe /T
    $batPath = "PUBLIC\scratch\launch_cdp_chrome.bat"
    Set-Content -Path $batPath -Value "@echo off`n`"C:\Program Files\Google\Chrome\Application\chrome.exe`" --remote-debugging-port=9222 --user-data-dir=`"$env:LOCALAPPDATA\Google\Chrome\Priya Automation Profile`""
    cmd /c "schtasks /delete /tn `"PriyaVisibleBrowser`" /f & schtasks /create /tn `"PriyaVisibleBrowser`" /tr `"\`"$batPath\`"`" /sc once /st 00:00 /it /f & schtasks /run /tn `"PriyaVisibleBrowser`""
    Start-Sleep -Seconds 4
}
```

---

## Step 5 — Write and Run the Fill Script

Create `PUBLIC\scratch\hardcoded_fill_JOB_ID.py`. It must:

```python
import os
import time
from playwright.sync_api import sync_playwright

url = "APPLY_URL"

with sync_playwright() as p:
    print("Connecting to Chrome over CDP...")
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    context = browser.contexts[0]

    # Reuse existing tab if URL matches, otherwise open a new one
    page = None
    for existing_page in context.pages:
        if url in existing_page.url:
            page = existing_page
            print("Reusing existing tab for this job!")
            page.bring_to_front()
            break

    if not page:
        print("Opening new page...")
        page = context.new_page()
        page.goto(url)
        page.set_default_timeout(3000)
        page.wait_for_load_state("load")
    else:
        page.set_default_timeout(3000)

    # ... hardcoded Playwright locators + fill/select calls for every field,
    #     using the values from JOB_ID_answers.json (or your own live reading
    #     of the form if no answers.json exists) ...
    # ... attach $resumePdf and $coverPdf to whatever file-upload input(s) exist ...

    print("Form filled. Disconnecting...")
    browser.close()
```

- Run the script: `python -u PUBLIC\scratch\hardcoded_fill_JOB_ID.py`
- **Form Filling Rules**:
  - Always use the LinkedIn profile link from Priya's resume for the LinkedIn profile field (do not leave it empty).
  - If asked about employment status, always answer "not currently employed."
  - If asked if the application can be used for other applications/future opportunities, always answer "yes" / agree to it.
  - If asked how she heard about the job, look up the `source` column for this job in `jobs.json` and use that value.
  - If asked for date of birth, leave the field blank and flag it as a placeholder for Priya to fill in manually — no date of birth is available in her profile data.
- **Never click the final submit button.** Leave the form filled and waiting for review.

---

## Step 6 — Hand off for manual review

Immediately after the fill script completes successfully (both modes) — this is what powers `firebase_app/review.html`'s "Filled Forms" tab, so Priya can find and confirm-submit it later even from a different machine/session than the one that filled it:
```powershell
$formFilled = [ordered]@{
    status = "pending_review"
    filled_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    done_at = $null
} | ConvertTo-Json -Compress
$tmpFile = "PUBLIC\scratch\form_filled_JOB_ID.json"
Set-Content -Path $tmpFile -Value $formFilled -Encoding utf8 -NoNewline
python job_status_store.py set --url "JOB_URL" --field form_filled --json --value-file $tmpFile
Remove-Item $tmpFile -ErrorAction SilentlyContinue
```
(JSON must go through `--value-file`, never an inline `--value` — see the note on this elsewhere in this file.)

Print:
```
JOB_ID (JOB_TITLE @ COMPANY) — form opened and filled at APPLY_URL.
Resume       : $resumePdf
Cover letter : $coverPdf
Review the filled form in the browser window, then click submit yourself — this skill never submits automatically.
It's now also listed under the "Filled Forms" tab in firebase_app/review.html — checking "Done" there marks it applied with today's date.
```

Wait for the user to confirm they've reviewed and submitted (or otherwise closed the browser) before considering the task done. Do not mark the job `applied` anywhere automatically — that's a separate, explicit action the user takes (via review.html's Filled Forms tab, or the dashboard's own Applied toggle).

**In `$AutoMode`**, there's nobody to wait for — after printing the hand-off message above, set `auto_fill_attempted_at` and stop the whole cycle (this job satisfies the "at least one filled form" goal; do not pick another candidate):
```powershell
python job_status_store.py set --url "JOB_URL" --field auto_fill_attempted_at --value (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
```

## Application Form Rules
- The field for LinkedIn profile is left empty by default on most forms — use Priya's LinkedIn profile link from her resume, do not leave it empty.
- She is not currently employed (last role at Topcon Healthcare ended July 2026).
- Her application can be used for other applications/future opportunities — agree to this if asked.
- Whenever asked how she heard about the job, use the `source` column corresponding to this job in the dashboard/jobs.json.
- No date of birth is available — leave blank and flag for manual completion if a form requires it.
