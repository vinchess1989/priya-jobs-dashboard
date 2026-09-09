Resolve a job posting URL down to the actual application form URL (or contact email), following "Apply" links/redirects across as many hops as needed, using a cached per-domain strategy so repeat visits to the same site skip straight to the fast path.

Arguments: **$ARGUMENTS**

Parse `$ARGUMENTS` as space-separated tokens:
- `BASE_URL` — the first token starting with `http://` or `https://`, if any.
- `JOB_ID` — the first remaining (non-URL) token, if any.

At least one of the two must be present — abort with an error otherwise.

**If `BASE_URL` was not given but `JOB_ID` was:** look it up. Read `PUBLIC\jobs.json` (read-only — see `JOBS_JSON` below) and find the entry whose `id` equals `JOB_ID`; use its `url` field as `BASE_URL`. If no job with that ID exists, abort with an error (`Job JOB_ID not found in jobs.json`) rather than guessing.

If `BASE_URL` *was* given explicitly, use it as-is even when `JOB_ID` is also present (an explicit URL always wins over the jobs.json lookup — useful when jobs.json's `url` is itself just a listing-aggregator link and the caller already has something more direct).

`JOB_ID`, whenever present, is also used to write the result back to Firestore (see Step 5; `jobs.json` itself is never written to).

Examples:
- `/find-apply-link https://tyomarkkinatori.fi/henkiloasiakkaat/avoimet-tyopaikat/e84e42ef-.../fi abc12345` — explicit URL + JOB_ID
- `/find-apply-link abc12345` — JOB_ID only; `BASE_URL` resolved from `jobs.json`

---

## Output contract (for use by other skills)

Any skill that says "apply the find-apply-link technique to URL X" should follow the steps below inline and expect these results at the end:

- `RESULT_TYPE` — one of `form`, `email`, `not_found`
- `RESULT` — the form URL, or the email address, or empty
- `HOPS` — number of pages visited to get there
- `STRATEGY` — `direct` | `webfetch_hops` | `api` | `network_intercept` | `headless_render` | `not_found`

Callers should treat `RESULT_TYPE: email` as "no form to fill — generate resume/cover letter only, then flag for manual email send," and `RESULT_TYPE: not_found` as "fall back to whatever the caller was already doing" (e.g. keyword-scanning the description, or using `JOB_URL` as-is).

---

## Why this order (token-cost rationale)

Discovering a site's real application endpoint the hard way (downloading a megabyte-plus JS bundle and grepping it for API paths) is the single most expensive thing this skill can do. Every step below is ordered cheapest-first, and step 0's cache means that cost is **paid at most once per domain, ever**:

1. **Cache first** — if we've already reverse-engineered this domain, skip straight to replaying the known recipe. No WebFetch, no raw HTML download, no JS bundle.
2. **WebFetch before raw HTTP** — WebFetch already converts HTML to markdown and summarizes with a small model in one call. Only fall back to raw `Invoke-WebRequest` + Grep when WebFetch reports it saw no real content (the SPA-shell signal).
3. **Grep over Read, always** — never `Read` a full downloaded HTML/JS file into context. Use `Grep` with narrow patterns and small `head_limit`s to find the 1–2 relevant lines, and only `Read` small extracted JSON.
4. **JS bundle inspection is last resort and write-once** — the moment a working API template is found, it's saved to `site_patterns.json` so no future job on that domain ever repeats this step.

---

## Constants

**`PUBLIC`** — repo root. `$PUBLIC = (Get-Location).Path`

**`SITE_PATTERNS`** = `PUBLIC\site_patterns.json` — the domain-strategy cache. Create it with `{}` if missing.

**`JOBS_JSON`** = `PUBLIC\jobs.json` — **read-only.** It's rewritten wholesale by the scraper on each run (once daily, via the Scheduled Task); this skill only ever reads it (to look up `JOB_ID`'s `url`, used as the Firestore key in Step 5) and must never write to it. See `CLAUDE.md` for why.

**`SCRATCH`** = `PUBLIC\scratch\find_apply_link\` — create if missing. Use a short name derived from the domain for temp files (e.g. `tyomarkkinatori_raw.html`, `tyomarkkinatori_widget.js`, `tyomarkkinatori_api.json`) so repeated runs overwrite instead of accumulating.

**`MAX_HOPS`** = 4 — abort if not resolved within this many page-to-page hops (loop protection: also abort immediately if a URL is visited twice).

**Known apply-keyword patterns** (use in WebFetch prompts and raw-HTML grep):
- Finnish: `Hae paikkaa`, `Hae työpaikkaa`, `Hae nyt`, `Jätä hakemus`, `hakemuslinkki`, `Hae tästä`, `Lähetä hakemus`
- English: `Apply now`, `Apply here`, `Apply at`, `Application link`, `Submit application`

---

## Step 0 — Load the domain-strategy cache

```powershell
$hostName = ([Uri]$BASE_URL).Host
```

Read `SITE_PATTERNS`. Look up `$hostName`.

- **Not found** → go to Step 1 (generic discovery).
- **Found, `"strategy": "api"`** → skip straight to Step 4 (Known API fast path).
- **Found, `"strategy": "network_intercept"`** → skip straight to Step 3.5, using the cached `intercept_hint` (e.g. LinkedIn's `companyApplyUrl` field over `voyager`/`graphql` responses — see `extract_company_apply_url.py` for a working reference implementation of this exact technique).
- **Found, `"strategy": "direct"` or `"webfetch_hops"`** → go to Step 1, but treat the cached `notes`/`typical_hop_count` as a hint (e.g. "this domain's real apply link is always behind the button matching `a:has-text("Hae paikkaa")`") to reduce back-and-forth guessing.

If `SITE_PATTERNS` doesn't exist yet, seed it from the known aggregator list already hardcoded in `fill_application.py` (`LISTING_DOMAINS`) so day one isn't a cold start:
```json
{
  "jobly.fi": {"strategy": "webfetch_hops", "notes": "listing aggregator — has an external apply link"},
  "duunitori.fi": {"strategy": "webfetch_hops", "notes": "listing aggregator — has an external apply link"},
  "indeed.com": {"strategy": "webfetch_hops", "notes": "listing aggregator — has an external apply link"},
  "monster.com": {"strategy": "webfetch_hops", "notes": "listing aggregator — has an external apply link"},
  "jobs.fi": {"strategy": "webfetch_hops", "notes": "listing aggregator — has an external apply link"},
  "te-palvelut.fi": {"strategy": "webfetch_hops", "notes": "legacy TE portal — may redirect to tyomarkkinatori.fi"},
  "tyomarkkinatori.fi": {
    "strategy": "api",
    "notes": "SPA; job content loaded client-side by the TmtTyopaikkaHakuV2 widget. Page HTML alone (even raw) has no job data.",
    "id_regex": "avoimet-tyopaikat/([0-9a-fA-F-]{36})",
    "api_template": "https://tyomarkkinatori.fi/api/jobposting-new/v1/public/jobpostings/{id}",
    "apply_json_path": "application.url.<any language key present, e.g. .fi>",
    "email_json_path": "recruiter.contacts[0].email"
  },
  "mol.fi": {"strategy": "webfetch_hops", "notes": "listing aggregator — has an external apply link"},
  "oikotie.fi": {"strategy": "webfetch_hops", "notes": "listing aggregator — has an external apply link"},
  "rekrytointi.fi": {"strategy": "webfetch_hops", "notes": "listing aggregator — has an external apply link"},
  "linkedin.com": {
    "strategy": "network_intercept",
    "notes": "companyApplyUrl only appears in voyager/graphql XHR responses, never in rendered HTML or WebFetch markdown.",
    "intercept_hint": "See extract_company_apply_url.py: launch Playwright with the saved LinkedIn session, listen for responses whose URL contains 'voyager' or 'graphql', regex the body for \"companyApplyUrl\":\"([^\"]+)\"."
  }
}
```

---

## Step 1 — WebFetch the current page (cheap, try before every fallback)

Set `CURRENT_URL = BASE_URL`, `HOP_COUNT = 0`, `VISITED = []`.

Loop:

1. If `CURRENT_URL` is in `VISITED` → abort: `RESULT_TYPE=not_found`, note "loop detected", go to Step 5.
2. Add `CURRENT_URL` to `VISITED`. If `HOP_COUNT > MAX_HOPS` → abort: `RESULT_TYPE=not_found`, note "max hops exceeded", go to Step 5.
3. Call **WebFetch** on `CURRENT_URL` with this prompt (keep it tight — the point is a one-line, cheap-to-parse answer, not a prose analysis):

   ```
   This is a job posting or job-application page. Reply with EXACTLY one line, nothing else:
   FORM_URL: <url>    — if THIS page itself contains a fillable application form (multiple input fields: name, email, resume/CV upload, etc.)
   NEXT_URL: <url>    — if there's a button or link to click before reaching the form (look for: Hae paikkaa, Hae työpaikkaa, Hae nyt, Apply now, Apply here, Apply at, Application link)
   EMAIL: <address>   — if the page says to send an application by email instead of a form
   NONE_FOUND         — if the page looks like only navigation/menu/site-shell content with no actual job posting or apply info (this usually means the real content is loaded by JavaScript and didn't make it into this fetch)
   ```

4. If WebFetch itself reports a cross-host redirect (per its own redirect-handling behavior) — treat the redirect target as `NEXT_URL` and continue the loop without spending an extra hop-classification round trip.
5. Parse the single-line response:
   - `FORM_URL: <url>` → `RESULT_TYPE=form`, `RESULT=<url>` (resolve relative to `CURRENT_URL` if not absolute). Go to Step 5.
   - `EMAIL: <address>` → `RESULT_TYPE=email`, `RESULT=<address>`. Go to Step 5.
   - `NEXT_URL: <url>` → `CURRENT_URL = <url>` (resolve relative if needed), `HOP_COUNT += 1`, repeat this loop.
   - `NONE_FOUND` → go to Step 2, **for this `CURRENT_URL`** (don't restart from `BASE_URL`).

---

## Step 2 — Raw HTML fallback (only when WebFetch says NONE_FOUND)

WebFetch can't see JavaScript-rendered content, so fetch the real HTML directly:

```powershell
$raw = Invoke-WebRequest -Uri $CURRENT_URL -UserAgent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36" -UseBasicParsing
$raw.Content | Out-File -FilePath "$SCRATCH\<domain>_raw.html" -Encoding utf8
```

**Do not `Read` this file.** Use `Grep` (narrow patterns, small `head_limit`) to check for, in order:

1. Embedded structured data: `__NEXT_DATA__`, `application/ld+json`, `window.__INITIAL_STATE__`, `__NUXT__` — if found, `Read` only the small matched region/offset and pull the apply URL / email straight out of that JSON.
2. A widget/bundle loader hint (e.g. `data-widget-path`, `data-widget-...`, a `<script src="...widget...">` tag) — this is the SPA signal. Note the bundle URL and go to Step 3.
3. Literal apply-keyword text (see the keyword list in Constants) followed by a URL or email on the same or next line — if found, use it directly, go to Step 5.

If none of the above → go to Step 3.5 (headless render) rather than giving up, since some SPAs render entirely client-side with no embedded state at all.

---

## Step 3 — JS bundle inspection (expensive — only once per domain, ever)

```powershell
Invoke-WebRequest -Uri $bundleUrl -UserAgent "Mozilla/5.0" -UseBasicParsing | Select-Object -ExpandProperty Content | Out-File "$SCRATCH\<domain>_widget.js" -Encoding utf8
```

This file can be 1MB+. **Never `Read` it.** Instead:

1. `Grep` for `"/api/[a-zA-Z0-9/_-]+"` with `-o` to list every distinct API path prefix referenced in the bundle.
2. `Grep` again, narrowed to whichever prefix looks job/application-related (e.g. containing `job`, `posting`, `tyopaikka`, `hakemus`, `application`), with a pattern like `<prefix>/v1[a-zA-Z0-9/_${}.-]*` to capture the exact path template (e.g. `.../public/jobpostings/`).
3. Extract the ID from `BASE_URL` (regex for a UUID or numeric segment in the path) and construct the candidate API URL.
4. Fetch it via `Invoke-WebRequest`, save the (small) JSON response to `$SCRATCH\<domain>_api.json`, and **this one you can `Read`** — it's small. Extract the apply URL and/or contact email from it.

If the constructed API URL 404s or 401s, try the sibling path variants seen in the same grep output (e.g. `-new` vs non-`-new` versions, `/public/` vs no `/public/`) before giving up.

Once a working template is confirmed, this domain's recipe goes into `site_patterns.json` in Step 5 so Step 3 is never repeated for it.

---

## Step 3.5 — Headless Playwright render (last resort)

Use when Steps 2–3 found no embedded state and no discoverable API (fully client-rendered page with no static hooks), or when the cached strategy is `network_intercept` (e.g. LinkedIn).

Write a short throwaway script to `SCRATCH\<domain>_render.py`:

```python
from playwright.sync_api import sync_playwright
import re, json

def run(url):
    hits = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # For network_intercept strategy (e.g. LinkedIn), reuse the saved session
        # and listen for the API responses instead of reading the DOM — see
        # extract_company_apply_url.py for the working pattern (voyager/graphql
        # responses containing "companyApplyUrl").
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(2000)
        html = page.content()
        browser.close()
    return html

if __name__ == "__main__":
    print(run("<<<CURRENT_URL>>>")[:0])  # adapt per-site: dump content or intercepted hits
```

Run it, then apply the same Grep-don't-Read discipline to whatever it outputs. This is the most expensive path (real browser boot) — confirm nothing cheaper was viable first.

---

## Step 4 — Known API fast path (cached domains)

Using the cached `id_regex`, extract the ID from `BASE_URL`. Build the URL from `api_template`. Fetch, save small JSON to `SCRATCH`, `Read` it, pull the value at `apply_json_path` (try each language key present, e.g. `.fi`/`.en`/`.sv`) — if empty or missing, fall back to `email_json_path`. Go to Step 5.

---

## Step 5 — Persist findings

**Update `SITE_PATTERNS`** — write or refresh the entry for `$hostName` with whatever strategy actually resolved this job (`direct` if WebFetch got it in one hop with no special handling needed, `webfetch_hops` with the hop count if it took following one or more apply buttons, `api`/`network_intercept` with the full recipe if Steps 3/3.5 discovered one). Always write this, even on a `not_found` outcome — record what was tried so a future run (or a human) doesn't repeat blind alleys, but don't mark a domain as permanently unresolvable; job sites change.

**If `JOB_ID` was given**, look up its `url` in `jobs.json` (read-only lookup — do not write to this file). If found:
- `RESULT_TYPE=form` → write `apply_url = RESULT` for that job URL.
- `RESULT_TYPE=email` → write `apply_email = RESULT` for that job URL.

Write it via `job_status_store.py` (Firestore `shared_state/job_status`, keyed by job URL) — **never** patch `jobs.json` directly; that file is scraper-owned and hand-patching it is what causes recurring merge conflicts with the scraper's own commits (see `CLAUDE.md`):
```powershell
python job_status_store.py set --url "$JOB_URL" --field $FIELD_NAME --value "$RESULT"
```

**`RESULT_TYPE=email` also needs an action item** — there's no form to fill for an email-only application, so it goes on the Action Items checklist (`firebase_app/review.html`) instead of getting silently lost. Skip this if an `action_item` is already recorded with `status: "done"` (don't reopen something already handled); otherwise write/refresh it:
```powershell
$existing = python job_status_store.py get --url "$JOB_URL" --field action_item
$alreadyDone = $false
if ($existing -ne "NONE") {
    try { $alreadyDone = ((ConvertFrom-Json $existing).status -eq "done") } catch {}
}
if (-not $alreadyDone) {
    $actionItem = [ordered]@{
        type       = "email_application"
        status     = "pending"
        created_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        done_at    = $null
        detail     = "Apply via email: $RESULT"
    } | ConvertTo-Json -Compress
    $tmpFile = "PUBLIC\scratch\action_item_$JOB_ID.json"
    Set-Content -Path $tmpFile -Value $actionItem -Encoding utf8 -NoNewline
    python job_status_store.py set --url "$JOB_URL" --field action_item --json --value-file $tmpFile
    Remove-Item $tmpFile -ErrorAction SilentlyContinue
}
```
(JSON must go through `--value-file`, not an inline `--value` — PowerShell 5.1 silently strips embedded double-quote characters from native-command arguments, so a raw JSON string never survives on the command line; see `job_status_store.py`'s own docstring.)

---

## Step 6 — Report

```
RESULT_TYPE : form | email | not_found
RESULT      : <url or email>
HOPS        : <n>
STRATEGY    : direct | webfetch_hops | api | network_intercept | headless_render | not_found
DOMAIN      : <hostname> (cached: yes/no)
```

If `not_found`, list what was tried (WebFetch hops visited, whether raw HTML / JS bundle / headless render were attempted) so the caller or the user can decide next steps.
