# Job Search Requirements — PLACEHOLDER, NOT YET CONFIGURED

**This file is a template. Replace every bracketed value below before running the scraper for
real — leaving it as-is will produce meaningless or empty results, since the LLM review step
matches every scraped job description against this file.**

## Candidate Profile
* **Name:** [PRIYA'S FULL NAME]
* **Current Role:** [ROLE]
* **Education:** [DEGREE(S), INSTITUTION(S), DATES]
* **Years of Experience:** [N]
* **Background:** [BACKGROUND SUMMARY]
* **Key Skills:** [SKILL1, SKILL2, ...]
* **Languages:** [LANGUAGE (level), ...]
* **Location / Relocation:** [WHERE SHE'S BASED, WHAT SHE'S OPEN TO]

## Hard Rejections
Immediately discard a job if ANY of the following are true — list every hard constraint here,
e.g. required degree/technical background she doesn't have, seniority level too high/low,
locations that don't work, keyword pre-filters, etc.:
* [HARD REJECTION 1]
* [HARD REJECTION 2]

## Target Job Criteria
A job is a match ("yes") if it satisfies ALL of the following categories. Use "maybe" for jobs
that are close but uncertain on one dimension, and explain why in each case.

**1. Domain / Role Type:** [FILL IN — what kind of roles/industries to target]

**2. Location & Work Model:** [FILL IN — countries/cities/remote acceptable]

**3. Experience Level:** [FILL IN — seniority band that fits]

**4. [ADDITIONAL CATEGORY IF NEEDED]:** [FILL IN]

## Agent Instructions
1. Read the full job posting text.
2. Apply the Hard Rejections above first — if any apply, the answer is "no", full stop.
3. Check Domain/Role Type, then Location & Work Model, then Experience Level.
4. Return a JSON object with `match` ("yes"/"maybe"/"no"), `reason` (one sentence), and the
   `posted_date`/`deadline`/`company`/`location` fields as instructed in the prompt itself.

### Automatically Added Negative Constraints (from UI Rejections):
