Tailor fresh resumes and cover letters for one or more job IDs using Claude, replacing any existing docs and updating the live dashboard. Does not touch application forms, scrape questions, or generate answers — use fill-form for that.

The job IDs to process are: **$ARGUMENTS**

Parse `$ARGUMENTS` as a space-separated list of job ID tokens (no URL parsing — this skill never visits an application form, so explicit apply URLs are not needed).

Examples:
- `abc123` → one job
- `abc123 def456` → two jobs

Run Steps 0–4 for **each job ID in sequence**, then run Steps 5–7 once at the end to batch-commit and sync everything.

---

## Constants (resolved at runtime — device-agnostic)

Resolve these once before starting the loop.

**`PUBLIC`** — the root of this repository. Derive from the skill file's own location (two levels up from `.claude/commands/`), or confirm with:
```powershell
$PUBLIC = (Get-Location).Path   # Claude is always invoked from the repo root
```

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
4. If still not found — stop and ask the user to set the `PRIYA_PRIVATE_DIR` environment variable to the correct path, then re-run.

**`JOBS_JSON`** = `PUBLIC\jobs.json`

Print the resolved paths before starting the loop:
```
PUBLIC  : <resolved path>
PRIVATE : <resolved path>
```

Read the structural template **once** before the loop:
Read `PRIVATE\Resumes\Master\master_data.json`. Every output JSON must match this structure exactly (same keys, same nesting).

---

## Checkpoint Check — runs once before the loop

Canonical step order: `0 → 1 → 2 → 3 → 4 → 4.5`

For each JOB_ID in the list, check whether `PRIVATE\Resumes\JOB_ID\JOB_ID_checkpoint.json` exists.

If **no** checkpoint exists for a job, set `START_STEP[JOB_ID] = "0"` (start fresh, no prompt).

If a checkpoint **does** exist, read it and display a block like this for each such job:

```
Checkpoint found — abc123 (DevOps Engineer @ ICEYE)
  Completed : 0, 1, 2, 3
  Last run  : 2026-09-09 10:30
  Next step : 4 (Generate PDFs)

  [Enter] Continue from Step 4        ← default
  [2]     Redo from Step 2 (Write tailored JSON)
  [1]     Redo from Step 1 (Fetch description)
  [0]     Start completely fresh
  [skip]  Skip this job this run
```

Ask the user for their choice for each job that has a checkpoint. Set `START_STEP[JOB_ID]` to the chosen step (or `"skip"` to exclude that job from the loop entirely).

If the user chooses `[0]` (fresh), delete the existing checkpoint file before entering the loop.

---

## Loop — repeat Steps 0–4 for each JOB_ID

**If `START_STEP[JOB_ID]` is `"skip"`, skip this job entirely.**

**Skip rule:** At the start of each step, if the step ID comes *before* `START_STEP[JOB_ID]` in the canonical order `[0, 1, 2, 3, 4, 4.5]`, print `↷ Skipping Step N (checkpoint)` and move to the next step. Step 4.5 is the one exception: it **always** runs whenever Step 4 runs (fresh or resumed) and is never skipped by checkpoint state, since it is what catches a broken Step 4 output.

**Checkpoint write rule:** After each step completes successfully, write or update `PRIVATE\Resumes\JOB_ID\JOB_ID_checkpoint.json`:
```json
{
  "job_id": "JOB_ID",
  "job_title": "JOB_TITLE",
  "company": "COMPANY",
  "updated_at": "<ISO timestamp>",
  "completed_steps": ["0", "1", ...]
}
```
Add the current step's ID to `completed_steps` if not already present. Preserve all previously completed steps.

---

### Step 0 — Find the job

Read `JOBS_JSON` and locate the entry where `"id"` equals `JOB_ID`.
If not found, skip this ID, print an error, and continue to the next.

Record:
- `JOB_TITLE`  — the `title` field
- `COMPANY`    — the `company` field
- `JOB_URL`    — the `url` field
- `DESC_FILE`  — the `description_file` field (may be null)

---

### Step 1 — Obtain the job description

Try in order, stopping at the first success:

1. If `DESC_FILE` is set, read `PUBLIC\DESC_FILE`. Accept it if it contains more than 200 meaningful words after the `JOB DESCRIPTION:` header (not cookie walls or login pages).
2. Use **WebFetch** on `JOB_URL`.
3. Try a web search for `"JOB_TITLE" "COMPANY" job"`.

If all three fail, skip this ID, report which sources were tried, and continue to the next.

---

### Step 2 — Write the tailored data.json

Create folder `PRIVATE\Resumes\JOB_ID\` if it does not exist.
Write the tailored JSON to `PRIVATE\Resumes\JOB_ID\JOB_ID_data.json` — overwrite if it exists.

#### Tailoring rules

English-only — Priya does not speak Finnish or Swedish, and her `job_requirements.md` already hard-rejects any job requiring either, so a job posting reaching this skill is always in English (or an English-language workplace). There is no language-detection branch here, unlike the equivalent skill for other candidates in this family of repos.

**Top-level fields:**
- `job_id`: set to `JOB_ID`
- `job_title`: set to `JOB_TITLE` (exact string from jobs.json)
- `company`: set to `COMPANY`
- `tailored_at`: set to the current ISO timestamp (e.g., `"2026-09-09T12:00:00+03:00"`) representing the exact time you are running this skill.
- `tailor_model`: set to `"claude-sonnet-4-6"`

**`resume.name`:** Always `"Priyanga Ramachandran"` — copy verbatim from the template. Never omit this field; `make_resume.py` silently renders a blank header line if it's missing.

**`resume.contact`:** Always copy the entire object verbatim from the template (`address`, `phone`, `email`, `linkedin_url`, `linkedin_display`) — these are static and never job-specific. Never omit this object; a missing `contact` silently renders a blank line under the name with no error. Note: there is no `date_of_birth` field in Priya's template (intentionally omitted) — do not invent one.

**`resume.role`:** `"JOB_TITLE Candidate"`, or a natural short variant of it (e.g. `"DevOps Engineer | Release & Configuration Management"` if the exact job title reads awkwardly as "X Candidate").

**`resume.profile`:** 2–3 sentences, highly specific to this role and company. Directly connect Priya's most relevant background (Configuration Management, Release Management, DevOps/CI-CD in regulated safety-critical industries) to the stated requirements. Do not just summarise her CV — name the company and what they need.

**`resume.experience`:** Keep all four entries exactly as in the template (same dates, companies, titles). Reorder the four entries so the most relevant experience appears first. Within each entry, reorder and reword the bullets to front-load skills mentioned in the job description.

**`resume.education`:** Keep both entries as in the template, **using the exact same keys** (`qual`, `inst`, and `bold` where set). Do not rename these keys (e.g. to `degree`/`school`) — `make_resume.py` reads `qual`/`inst` specifically and silently renders blank rows for any other key names.

**`resume.languages_html`:** Copy verbatim from the template — English only.

**`resume.competencies_html`:** Completely rewrite 4–5 skill categories that map directly onto the key requirements in this job description (e.g. CI/CD Pipelines, Release Management, Configuration Management, Quality & Compliance, Scripting & Automation — pick whichever subset the posting actually asks for). Use `<span class="skill-cat">Category:</span> description...` format.

**`resume.references`:** Always copy the entire array verbatim from the template (same names, titles, contacts). Never omit this array; a missing `references` silently renders an empty "REFERENCES" section heading with no content and no error.

Note: Priya's template has no `wage_subsidy_note`, `volunteering`, `achievements`, `publications_html`, or `labels` fields — do not invent content for these; simply omit them (or copy them if a later edit to `master_data.json` ever adds real content there).

**`cover_letter.date`:** Use today's date formatted as `"9 September 2026"`.

**`cover_letter.recipient`:** Fill `company` with `COMPANY` and `city` with the job location. Use `"Hiring Manager"` for title if no name is known.

**`cover_letter.paragraphs`:** 4–5 paragraphs:
  1. Hook — what drew Priya to this company and role specifically.
  2. Most relevant experience — connect it directly to the job requirements (Elektrobit/Topcon release, CM, or DevOps work, whichever maps best).
  3. Why this company — something specific from the posting or company.
  4. Working language / location — she's based in Oulu, Finland, and has worked fully in English throughout her career there; mention willingness to relocate within Finland or work remotely across the EU if relevant to this posting.
  5. Close — availability (next possible working day, since she's not currently employed), contact invitation.

**`cover_letter.sign_off`:** `"Yours sincerely"`.

#### Priya's profile (use exactly these facts)
- 15 years of experience in Configuration Management, Release Management, DevOps/CI-CD, and earlier-career software engineering.
- Technical Communication Specialist & Product Specialist — Topcon Healthcare Solutions EMEA Oy, Oulu (Sep 2025 – Jul 2026): quality gatekeeper on the RDx tele-refraction/clinical-workflow platform (MDR/ISO 13485 regulated) — reviewed change documentation, validated approvals, owned traceability records, coordinated release readiness across dev/QA/product/regulatory teams.
- Senior Software Engineer — Release & Configuration Management, Elektrobit Automotive Finland Oy, Oulu (Aug 2022 – Jun 2025): owned end-to-end release execution for EB Corbos Hypervisor & EB Corbos Linux (ASPICE/TUV automotive-grade safety-critical products), built/ran CI/CD pipelines (Jenkins, GitHub Actions), defined branching/baselining strategy, wrote Python/Bash release automation, supported ASPICE/TUV external audits (achieved RFM quality level).
- Technical Writing & Translation Specialist / Application Development Specialist, Accenture (Apr 2016 – Apr 2025): owned release documentation each sprint on Salesforce-based platforms, certified Salesforce Admin/App Builder.
- Software Engineer, Pronto Software Solutions Pvt. Ltd., India (Dec 2013 – Apr 2015): built Windows desktop apps in C#/C++.
- Certifications: Salesforce Administrator (ADM 201), Salesforce Platform App Builder (DEX 402), Microsoft Azure Fundamentals.
- English fluent/professional working proficiency. Does **not** speak Finnish or Swedish — both her Finland-based employers (Topcon, Elektrobit) operate fully in English.
- Based in Oulu, Finland. Open to roles anywhere in Finland (any work model) and remote roles based in the EU/Europe more broadly (see `job_requirements.md` for the exact scope).
- Not currently employed — her last role (Topcon) ended July 2026. Available: next possible working day.
- Has the right to work in Finland (previously held valid work authorization there through her prior employment).
- References: Vili Lang (Line Manager, Elektrobit) — vili.lang@elektrobit.com; Morgane Fleuriot Pajunen (Engineering Manager, Topcon Healthcare) — Mob: 0504840007; Riitta Kasoli (QARA Specialist, Topcon Healthcare) — riitta.kasoli@topcon.com.
- No wage-subsidy (palkkatuki) eligibility is claimed for her — do not add a wage-subsidy paragraph or banner to any resume/cover letter.

---

### Step 3 — Clear old generated files

```powershell
Remove-Item "PRIVATE\Resumes\JOB_ID\*.html" -ErrorAction SilentlyContinue
Remove-Item "PRIVATE\Resumes\JOB_ID\*.pdf"  -ErrorAction SilentlyContinue
```

---

### Step 4 — Generate HTML and convert to PDF

```powershell
python "PUBLIC\make_resume.py" "PRIVATE\Resumes\JOB_ID\JOB_ID_data.json" --photo "PRIVATE\priya_photo.jpg" --out-dir "PRIVATE\Resumes"
```

This produces two `.html` files inside `PRIVATE\Resumes\JOB_ID\`. Convert each to PDF:

```powershell
$htmlFiles = Get-ChildItem "PRIVATE\Resumes\JOB_ID\*.html"
foreach ($html in $htmlFiles) {
    python "PUBLIC\html_to_pdf.py" $html.FullName
}
```

Confirm both PDF files exist. If either is missing, report the error but continue processing remaining job IDs.

Print a one-line progress note after each job: `✓ JOB_ID (JOB_TITLE @ COMPANY) — PDFs generated`

---

### Step 4.5 — Validate the generated resume

`make_resume.py` has no required fields — every value defaults to `""` if a key is missing or misnamed, so a broken `data.json` produces a resume that *looks* generated (files exist, PDF opens fine) but has silently blank sections. This step exists to catch that before it goes anywhere.

**Always run this step whenever Step 4 runs** — never skip it, including on a checkpoint-resume.

1. Read `PRIVATE\Resumes\JOB_ID\JOB_ID_data.json` and confirm ALL of the following are present and non-empty:
   - `resume.name`
   - `resume.contact.address`, `resume.contact.phone`, `resume.contact.email`
   - `resume.education` — non-empty array, and **every** entry has non-empty `qual` and `inst`
   - `resume.experience` — non-empty array, and every entry has non-empty `title`, `company`, `dates`, and at least one bullet
   - `resume.languages_html`, `resume.competencies_html`
   - `resume.references` — non-empty array, and every entry has non-empty `name`, `title`, `contact`
   - `cover_letter.paragraphs` — non-empty array with at least 3 paragraphs

2. If anything fails: fix `JOB_ID_data.json` (pull `name`/`contact`/`references` verbatim from the template read at the start of this skill; rename any wrong education keys to `qual`/`inst`), then redo Step 3 and Step 4 to regenerate, and re-check from the top of this step.

3. Once the JSON check passes, read the regenerated resume PDF itself (via the Read tool) and visually confirm PROFESSIONAL PROFILE, EDUCATION, and REFERENCES actually contain rendered text — not just present-but-empty section headings. This catches template/rendering bugs that a JSON-only check would miss.

Do not proceed to the next job or to Step 5 until validation passes. Print: `✓ JOB_ID — resume validated (all sections populated)`.

---

## End of loop

---

## Step 5 — Commit all jobs to private repo (one commit)

```powershell
git -C "PRIVATE" add Resumes\
git -C "PRIVATE" commit -m "Tailor resumes for N jobs: JOB_ID_1, JOB_ID_2, ... (claude-sonnet-4-6)"
git -C "PRIVATE" push
```

Use the actual count and list of successfully processed job IDs in the commit message.

---

## Step 6 — Sync links to Firestore (force overwrite)

```powershell
Set-Location "PUBLIC"
python sync_resume_links.py --upload --force
```

This rescans all Resumes/ folders, writes `input.csv`, and pushes the new PDF GitHub URLs to Firestore — overwriting any previously stored links for these jobs.

---

## Step 7 — Commit public repo

```powershell
git -C "PUBLIC" add input.csv
git -C "PUBLIC" commit -m "Update resume links for JOB_ID_1 JOB_ID_2 ... (retailored with claude-sonnet-4-6)"
git -C "PUBLIC" push origin main
```

If `input.csv` has no changes, skip the commit and note that it was already up to date.

---

## Step 8 — Report

Print a summary table for all processed jobs:

| Job ID | Title | Company | Resume PDF | Cover Letter PDF | Firestore |
|--------|-------|---------|------------|------------------|-----------|
| abc123 | ... | ... | filename.pdf | filename.pdf | ✓ |

Then note: "Dashboard links will appear live in the Docs column within ~30 seconds (Firebase realtime sync)."

If any job was skipped, list them with the reason.
