#!/usr/bin/env python3
"""
Convert HTML resume/cover letter files to PDF using Playwright (headless Chromium).

Usage:
    python html_to_pdf.py file1.html [file2.html ...]

Output: same path as input with .pdf extension.

Requires:
    pip install playwright
    playwright install chromium
"""

import sys
import os
import argparse
from pathlib import Path


def html_to_pdf(html_path: str) -> str:
    from playwright.sync_api import sync_playwright

    html_path = os.path.abspath(html_path)
    pdf_path = str(Path(html_path).with_suffix(".pdf"))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"file:///{html_path.replace(os.sep, '/')}", wait_until="networkidle")
        page.pdf(
            path=pdf_path,
            format="A4",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        browser.close()

    print(f"  PDF: {pdf_path}")
    return pdf_path


def main():
    parser = argparse.ArgumentParser(description="Convert HTML files to PDF via Playwright.")
    parser.add_argument("files", nargs="+", help="HTML file(s) to convert")
    args = parser.parse_args()

    for f in args.files:
        if not os.path.exists(f):
            print(f"ERROR: File not found: {f}")
            continue
        html_to_pdf(f)


if __name__ == "__main__":
    main()
