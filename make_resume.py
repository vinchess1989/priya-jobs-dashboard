#!/usr/bin/env python3
"""
Generate 1-page A4 HTML resume AND cover letter from a single job data JSON.

Usage:
    python make_resume.py <JOBID_data.json> --photo <path/to/photo.jpg> --out-dir <Resumes/>

Output (inside out-dir/<job_id>/):
    Priyanga_Ramachandran_<JobTitle>_<Company>_resume.html
    Priyanga_Ramachandran_<JobTitle>_<Company>_cover_letter.html

Then convert to PDF:
    python html_to_pdf.py <resume.html> <cover_letter.html>

Data JSON structure:
{
  "job_id": "f6aaa66f",
  "job_title": "Legal Trainee",
  "company": "Hiab",
  "resume": {
    "name": "Manju Krishna Haridas",
    "role": "Legal Trainee Candidate",
    "contact": { "address": "...", "phone": "...", "email": "...",
                 "linkedin_url": "...", "linkedin_display": "..." },
    "wage_subsidy_note": "..." (optional — rendered as a highlighted banner
                                 under the header, above Professional Profile),
    "profile": "...",
    "experience": [
      { "title": "...", "company": "...", "dates": "...", "bullets": ["..."] }
    ],
    "education": [
      { "qual": "...", "inst": "...", "bold": false }
    ],
    "languages_html": "...",
    "competencies_html": "...",
    "references": [
      { "name": "...", "title": "...", "contact": "..." }
    ],
    "volunteering": [
      { "title": "...", "org": "...", "dates": "...", "desc": "..." }
    ],
    "achievements": ["...", "..."] (each rendered with a medal icon),
    "publications_html": "...",
    "labels": { "profile": "...", ... } (optional — overrides section heading
                text per key for localized versions; see DEFAULT_LABELS for keys
                and English defaults; icons stay the same regardless of language)
  },
  "cover_letter": {
    "date": "23 June 2026",
    "recipient": { "title": "Hiring Manager", "team": "...", "company": "...", "city": "..." },
    "paragraphs": ["...", "..."],
    "sign_off": "Yours sincerely",
    "salutation": "..." (optional — full salutation line, e.g. "Hyvä vastaanottaja,";
                          defaults to "Dear {recipient.title},")
  }
}
"""

import json
import base64
import sys
import os
import re
import argparse
from pathlib import Path

DEFAULT_PHOTO = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "Priya_jobs_private", "priya_photo.jpg"
)

# ── SECTION ICONS (inline SVG, theme blue #1a4f82) ───────────────────────────

_ICON_ATTRS = 'width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#1a4f82" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"'

ICON_PROFILE = f'<svg {_ICON_ATTRS}><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.8-7.5 8-7.5s8 3.1 8 7.5"/></svg>'
ICON_EXPERIENCE = f'<svg {_ICON_ATTRS}><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5.5A1.5 1.5 0 0 1 9.5 4h5A1.5 1.5 0 0 1 16 5.5V7"/><path d="M3 12h18"/></svg>'
ICON_EDUCATION = f'<svg {_ICON_ATTRS}><path d="M2 9l10-5 10 5-10 5-10-5z"/><path d="M6 11.5V17c0 1.4 2.7 3 6 3s6-1.6 6-3v-5.5"/></svg>'
ICON_LANGUAGES = f'<svg {_ICON_ATTRS}><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.8 2.4 4.3 5.6 4.3 9s-1.5 6.6-4.3 9c-2.8-2.4-4.3-5.6-4.3-9S9.2 5.4 12 3z"/></svg>'
ICON_VOLUNTEERING = f'<svg {_ICON_ATTRS}><path d="M12 20.5s-7.5-4.9-7.5-10A4.5 4.5 0 0 1 12 7.5a4.5 4.5 0 0 1 7.5 3c0 5.1-7.5 10-7.5 10z"/></svg>'
ICON_COMPETENCIES = f'<svg {_ICON_ATTRS}><path d="M12 3v17M7 21h10"/><path d="M4 7h6M14 7h6"/><path d="M4 7l-2.5 5.5a2.7 2.7 0 0 0 5 0z"/><path d="M20 7l-2.5 5.5a2.7 2.7 0 0 0 5 0z"/></svg>'
ICON_ACHIEVEMENTS = f'<svg {_ICON_ATTRS}><path d="M7 4h10v4a5 5 0 0 1-10 0V4z"/><path d="M7 5H4.5A1.5 1.5 0 0 0 3 6.5 5 5 0 0 0 7 11M17 5h2.5A1.5 1.5 0 0 1 21 6.5 5 5 0 0 1 17 11"/><path d="M12 13v4M8.5 21h7"/></svg>'
ICON_PUBLICATIONS = f'<svg {_ICON_ATTRS}><path d="M4 4.5A1.5 1.5 0 0 1 5.5 3H18a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H5.5A1.5 1.5 0 0 0 4 18.5z"/><path d="M8 7.5h8M8 11h8"/></svg>'
ICON_REFERENCES = f'<svg {_ICON_ATTRS}><circle cx="9" cy="8" r="3"/><path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5"/><circle cx="17.5" cy="7" r="2.2"/><path d="M15.3 12c2.6.3 4.7 2 4.7 4.3"/></svg>'

MEDAL_ICON = (
    '<svg width="11" height="11" viewBox="0 0 24 24">'
    '<path d="M9 2L7 9M15 2l2 7" stroke="#c9971e" stroke-width="2" stroke-linecap="round" fill="none"/>'
    '<circle cx="12" cy="15" r="7" fill="#f6cf5c" stroke="#c9971e" stroke-width="1.6"/>'
    '<path d="M12 11.2l1.1 2.3 2.5.4-1.8 1.8.4 2.5-2.2-1.2-2.2 1.2.4-2.5-1.8-1.8 2.5-.4z" fill="#c9971e"/>'
    '</svg>'
)

# ── SECTION LABELS (overridable per-language via resume.labels) ─────────────

DEFAULT_LABELS = {
    "profile": "Professional Profile",
    "experience": "Professional Experience",
    "education": "Education",
    "languages": "Languages &amp; Skills",
    "volunteering": "Volunteering",
    "competencies": "Core Competencies",
    "achievements": "Achievements",
    "publications": "Publications",
    "references": "References",
}

# ── RESUME TEMPLATE ──────────────────────────────────────────────────────────

RESUME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{name} — CV</title>
<style>
@page {{ size: A4; margin: 0; }}
@media print {{
  body {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html {{ background: linear-gradient(160deg, #ffffff 0%, #f6f9fc 45%, #eaf1f8 100%); }}
body {{
  font-family: 'Calibri', 'Segoe UI', Arial, sans-serif;
  font-size: 9.2pt; line-height: 1.26; color: #1e1e1e;
  background: linear-gradient(160deg, #ffffff 0%, #f6f9fc 45%, #eaf1f8 100%);
  width: 210mm; min-height: 297mm; margin: 0 auto;
  padding: 8mm 12mm 7mm 12mm;
}}
.header {{
  display: flex; align-items: flex-start; gap: 14px;
  padding-bottom: 5px; border-bottom: 2.5px solid #1a4f82; margin-bottom: 5px;
}}
.photo {{
  width: 74px; height: 93px; object-fit: cover;
  object-position: top center; border-radius: 3px;
  flex-shrink: 0; border: 1px solid #ccc;
}}
.header-text {{ flex: 1; }}
.header-text h1 {{
  font-size: 17.5pt; font-weight: 800; color: #0e2d50;
  letter-spacing: 0.4px; line-height: 1;
}}
.header-text .role {{
  font-size: 10pt; color: #1a4f82; font-weight: 600; margin: 3px 0 6px;
}}
.header-text .contact {{ font-size: 8.3pt; color: #444; line-height: 1.65; }}
.header-text .contact a {{ color: #1a4f82; text-decoration: none; }}
h2 {{
  display: flex; align-items: center; gap: 4px;
  font-size: 8.6pt; font-weight: 800; color: #1a4f82;
  text-transform: uppercase; letter-spacing: 0.9px;
  border-bottom: 1px solid #1a4f82; padding-bottom: 1px; margin: 4px 0 2px;
}}
h2 svg {{ flex-shrink: 0; }}
.profile-text {{ font-size: 8.6pt; line-height: 1.32; color: #2a2a2a; }}
.job {{ margin-bottom: 3px; }}
.job-header {{ display: flex; justify-content: space-between; align-items: baseline; }}
.job-title {{ font-weight: 700; font-size: 9pt; }}
.job-co {{ font-style: italic; color: #444; font-size: 8.6pt; }}
.job-date {{ font-size: 8pt; color: #666; white-space: nowrap; padding-left: 6px; }}
ul {{ padding-left: 13px; margin-top: 1px; }}
li {{ font-size: 8.2pt; line-height: 1.28; margin-bottom: 0.5px; }}
.two-col {{ display: flex; gap: 14px; margin-top: 1px; }}
.col-left {{ flex: 0 0 50%; }}
.col-right {{ flex: 1; }}
.edu-table {{ width: 100%; border-collapse: collapse; }}
.edu-table tr {{ vertical-align: top; }}
.edu-table td {{ font-size: 8pt; line-height: 1.22; padding: 1px 2px; }}
.edu-table .qual {{ font-weight: 600; }}
.edu-table .inst {{ color: #555; text-align: right; white-space: nowrap; padding-left: 4px; }}
.highlight {{ color: #0e2d50; }}
.skill-block {{ font-size: 8.2pt; line-height: 1.35; }}
.skill-cat {{ font-weight: 700; color: #1a4f82; }}
.ref {{ font-size: 8pt; line-height: 1.25; }}
.ref-name {{ font-weight: 700; }}
.ref-row {{ display: flex; gap: 16px; margin-top: 2px; }}
.ref-row .ref {{ flex: 1; }}
.ach-item {{ display: flex; align-items: flex-start; gap: 4px; margin-bottom: 2px; }}
.ach-item svg {{ flex-shrink: 0; margin-top: 1.5px; }}
.vol {{ margin-bottom: 2.5px; }}
.vol-header {{ display: flex; justify-content: space-between; align-items: baseline; }}
.vol-title {{ font-weight: 700; font-size: 8pt; }}
.vol-date {{ font-size: 7.7pt; color: #666; white-space: nowrap; padding-left: 6px; }}
.vol-desc {{ font-size: 7.8pt; line-height: 1.25; color: #444; margin-top: 0.5px; }}
.highlight-banner {{
  background: #fff8e1; border-left: 3px solid #f59e0b; border-radius: 2px;
  padding: 3px 8px; margin-bottom: 4px; font-size: 8.2pt; font-weight: 700; color: #7a4a00;
}}
</style>
</head>
<body>

<div class="header">
  <img class="photo" src="data:image/jpeg;base64,{photo_b64}" alt="{name}">
  <div class="header-text">
    <h1>{name_upper}</h1>
    <div class="role">{role}</div>
    <div class="contact">
      {address}<br>
      {phone} &nbsp;|&nbsp; <a href="mailto:{email}">{email}</a> &nbsp;|&nbsp;
      <a href="{linkedin_url}">{linkedin_display}</a>
    </div>
  </div>
</div>

{wage_highlight_html}
<h2>{icon_profile}{label_profile}</h2>
<div class="profile-text">{profile}</div>

<h2>{icon_experience}{label_experience}</h2>
{experience_html}

<div class="two-col">
<div class="col-left">
<h2>{icon_education}{label_education}</h2>
<table class="edu-table">
{education_html}
</table>
<h2>{icon_languages}{label_languages}</h2>
<div class="skill-block">{languages_html}</div>
<h2>{icon_volunteering}{label_volunteering}</h2>
{volunteering_html}
</div>
<div class="col-right">
<h2>{icon_competencies}{label_competencies}</h2>
<div class="skill-block">{competencies_html}</div>
<h2>{icon_achievements}{label_achievements}</h2>
<div class="skill-block">{achievements_html}</div>
<h2>{icon_publications}{label_publications}</h2>
<div class="skill-block">{publications_html}</div>
</div>
</div>

<h2>{icon_references}{label_references}</h2>
<div class="ref-row">
{references_html}
</div>

</body>
</html>"""

# ── COVER LETTER TEMPLATE ─────────────────────────────────────────────────────

COVER_LETTER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{name} — Cover Letter</title>
<style>
@page {{ size: A4; margin: 20mm 22mm 18mm 22mm; }}
@media print {{
  body {{ margin: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Calibri', 'Segoe UI', Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.5; color: #1e1e1e;
  background: #fff; width: 166mm; margin: 0 auto;
}}
.sender {{
  font-size: 10pt; color: #1a4f82; font-weight: 600;
  border-bottom: 2px solid #1a4f82; padding-bottom: 6px; margin-bottom: 16px;
}}
.sender .name {{ font-size: 14pt; font-weight: 800; color: #0e2d50; }}
.sender .contact {{ font-size: 9pt; color: #444; margin-top: 3px; }}
.sender .contact a {{ color: #1a4f82; text-decoration: none; }}
.date {{ font-size: 10pt; color: #444; margin-bottom: 14px; }}
.recipient {{ font-size: 10pt; margin-bottom: 14px; line-height: 1.6; }}
.salutation {{ margin-bottom: 12px; font-weight: 600; }}
.body p {{ margin-bottom: 10px; text-align: justify; }}
.sign-off {{ margin-top: 20px; }}
.sign-name {{ font-weight: 800; font-size: 11pt; color: #0e2d50; margin-top: 6px; }}
</style>
</head>
<body>

<div class="sender">
  <div class="name">{name}</div>
  <div class="contact">
    {address} &nbsp;|&nbsp; {phone}<br>
    <a href="mailto:{email}">{email}</a> &nbsp;|&nbsp;
    <a href="{linkedin_url}">{linkedin_display}</a>
  </div>
</div>

<div class="date">{date}</div>

<div class="recipient">
  {recipient_title}<br>
  {recipient_team}<br>
  {recipient_company}, {recipient_city}
</div>

<div class="salutation">{salutation}</div>

<div class="body">
{paragraphs_html}
</div>

<div class="sign-off">
  {sign_off},<br>
  <div class="sign-name">{name}</div>
</div>

</body>
</html>"""


def slugify(text):
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")


def photo_to_b64(photo_path):
    with open(photo_path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def render_experience(jobs):
    parts = []
    for j in jobs:
        bullets = "\n".join(f"      <li>{b}</li>" for b in j.get("bullets", []))
        parts.append(
            f'  <div class="job">\n'
            f'    <div class="job-header">\n'
            f'      <span class="job-title">{j.get("title", "")}</span>\n'
            f'      <span class="job-date">{j.get("dates", "")}</span>\n'
            f'    </div>\n'
            f'    <div class="job-co">{j.get("company", "")}</div>\n'
            f'    <ul>\n{bullets}\n    </ul>\n'
            f'  </div>'
        )
    return "\n".join(parts)


def render_education(rows):
    parts = []
    for r in rows:
        q = f"<strong>{r.get('qual', '')}</strong>" if r.get("bold") else r.get("qual", "")
        parts.append(
            f'  <tr><td class="qual">{q}</td>'
            f'<td class="inst">{r.get("inst", "")}</td></tr>'
        )
    return "\n".join(parts)


def render_references(refs):
    return "\n".join(
        f'  <div class="ref"><div class="ref-name">{r.get("name", "")}</div>'
        f'{r.get("title", "")}<br>{r.get("contact", "")}</div>'
        for r in refs
    )


def render_volunteering(rows):
    parts = []
    for v in rows:
        parts.append(
            f'  <div class="vol">\n'
            f'    <div class="vol-header">\n'
            f'      <span class="vol-title">{v.get("title", "")} — {v.get("org", "")}</span>\n'
            f'      <span class="vol-date">{v.get("dates", "")}</span>\n'
            f'    </div>\n'
            f'    <div class="vol-desc">{v.get("desc", "")}</div>\n'
            f'  </div>'
        )
    return "\n".join(parts)


def render_achievements(items):
    return "\n".join(
        f'  <div class="ach-item">{MEDAL_ICON}<span>{item}</span></div>'
        for item in items
    )


def render_paragraphs(paras):
    return "\n".join(f"  <p>{p}</p>" for p in paras)


def generate(data_path, photo_path, out_dir):
    with open(data_path, encoding="utf-8") as f:
        d = json.load(f)

    job_id = d.get("job_id", "unknown_job")
    job_title = d.get("job_title", "Unknown Role")
    company = d.get("company", "Unknown Company")
    r = d.get("resume", {})
    cl = d.get("cover_letter", {})

    b64 = photo_to_b64(photo_path)

    # ── File naming ───────────────────────────────────────────────────────────
    slug = "Priyanga_Ramachandran"
    folder = os.path.join(out_dir, job_id)
    os.makedirs(folder, exist_ok=True)

    # ── Resume HTML ───────────────────────────────────────────────────────────
    rc = r.get("contact", {})
    wage_note = r.get("wage_subsidy_note", "")
    wage_highlight_html = f'<div class="highlight-banner">{wage_note}</div>' if wage_note else ""
    labels = {**DEFAULT_LABELS, **r.get("labels", {})}
    resume_html = RESUME_HTML.format(
        name=r.get("name", "Priyanga Ramachandran"),
        name_upper=r.get("name", "Priyanga Ramachandran").upper(),
        role=r.get("role", ""),
        address=rc.get("address", ""),
        phone=rc.get("phone", ""),
        email=rc.get("email", ""),
        linkedin_url=rc.get("linkedin_url", ""),
        linkedin_display=rc.get("linkedin_display", ""),
        wage_highlight_html=wage_highlight_html,
        profile=r.get("profile", ""),
        experience_html=render_experience(r.get("experience", [])),
        education_html=render_education(r.get("education", [])),
        languages_html=r.get("languages_html", ""),
        competencies_html=r.get("competencies_html", ""),
        references_html=render_references(r.get("references", [])),
        volunteering_html=render_volunteering(r.get("volunteering", [])),
        achievements_html=render_achievements(r.get("achievements", [])),
        publications_html=r.get("publications_html", ""),
        icon_profile=ICON_PROFILE,
        icon_experience=ICON_EXPERIENCE,
        icon_education=ICON_EDUCATION,
        icon_languages=ICON_LANGUAGES,
        icon_volunteering=ICON_VOLUNTEERING,
        icon_competencies=ICON_COMPETENCIES,
        icon_achievements=ICON_ACHIEVEMENTS,
        icon_publications=ICON_PUBLICATIONS,
        icon_references=ICON_REFERENCES,
        label_profile=labels["profile"],
        label_experience=labels["experience"],
        label_education=labels["education"],
        label_languages=labels["languages"],
        label_volunteering=labels["volunteering"],
        label_competencies=labels["competencies"],
        label_achievements=labels["achievements"],
        label_publications=labels["publications"],
        label_references=labels["references"],
        photo_b64=b64,
    )
    resume_out = os.path.join(folder, f"{slug}_resume.html")
    with open(resume_out, "w", encoding="utf-8") as f:
        f.write(resume_html)
    print(f"  Resume HTML : {resume_out}")

    # ── Cover letter HTML ─────────────────────────────────────────────────────
    rec = cl.get("recipient", {})
    salutation = cl.get("salutation") or f"Dear {rec.get('title', '')},"
    cl_html = COVER_LETTER_HTML.format(
        name=r.get("name", "Priyanga Ramachandran"),
        address=rc.get("address", ""),
        phone=rc.get("phone", ""),
        email=rc.get("email", ""),
        linkedin_url=rc.get("linkedin_url", ""),
        linkedin_display=rc.get("linkedin_display", ""),
        date=cl.get("date", ""),
        recipient_title=rec.get("title", ""),
        recipient_team=rec.get("team", ""),
        recipient_company=rec.get("company", ""),
        recipient_city=rec.get("city", ""),
        paragraphs_html=render_paragraphs(cl.get("paragraphs", [])),
        sign_off=cl.get("sign_off", "Yours sincerely"),
        salutation=salutation,
    )
    cl_out = os.path.join(folder, f"{slug}_cover_letter.html")
    with open(cl_out, "w", encoding="utf-8") as f:
        f.write(cl_html)
    print(f"  Letter HTML : {cl_out}")

    return resume_out, cl_out


def main():
    parser = argparse.ArgumentParser(
        description="Generate resume + cover letter HTML from a job data JSON."
    )
    parser.add_argument("data", help="Path to JOBID_data.json")
    parser.add_argument(
        "--photo",
        default=DEFAULT_PHOTO,
        help="Path to candidate photo (JPEG). Default: ../Priya_jobs_private/priya_photo.jpg",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Base output directory (Resumes/ folder). Defaults to sibling ../Priya_jobs_private/Resumes/",
    )
    args = parser.parse_args()

    out_dir = args.out_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "Priya_jobs_private", "Resumes"
    )

    print(f"Generating documents for: {args.data}")
    generate(args.data, args.photo, out_dir)
    print("Done. Run html_to_pdf.py on the HTML files to produce PDFs.")


if __name__ == "__main__":
    main()
