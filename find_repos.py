#!/usr/bin/env python3
"""
Locate the public and private priya-jobs repos on the current machine by
scanning common directories for git remotes matching known GitHub slugs.

Usage:
    python find_repos.py              # prints paths as KEY=value
    python find_repos.py --json       # prints JSON
    python find_repos.py --check      # exits 0 if both found, 1 if any missing
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

PUBLIC_SLUG  = "vinchess1989/priya-jobs-dashboard"
PRIVATE_SLUG = "vinchess1989/Priya-jobs-private"

SEARCH_ROOTS = [
    Path.home(),
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Projects",
    Path.home() / "dev",
    Path.home() / "repos",
    Path("C:/Users"),          # Windows: scan all user dirs one level deep
    Path("/home"),             # Linux
    Path("/Users"),            # macOS
]

MAX_DEPTH = 4  # don't recurse too deep


def get_remote_url(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=3
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def slug_matches(url: str, slug: str) -> bool:
    slug_lower = slug.lower()
    return slug_lower in url.lower()


def find_git_dirs(root: Path, depth: int) -> list[Path]:
    if depth == 0 or not root.is_dir():
        return []
    results = []
    try:
        for entry in root.iterdir():
            try:
                if not entry.is_dir():
                    continue
                if (entry / ".git").is_dir():
                    results.append(entry)
                elif entry.name not in (".git", "node_modules", "__pycache__", ".venv", "venv"):
                    results.extend(find_git_dirs(entry, depth - 1))
            except OSError:
                continue
    except OSError:
        pass
    return results


def locate_repos() -> dict[str, str | None]:
    found = {PUBLIC_SLUG: None, PRIVATE_SLUG: None}
    seen: set[Path] = set()

    for root in SEARCH_ROOTS:
        for git_dir in find_git_dirs(root, MAX_DEPTH):
            if git_dir in seen:
                continue
            seen.add(git_dir)
            url = get_remote_url(git_dir)
            if not url:
                continue
            for slug in (PUBLIC_SLUG, PRIVATE_SLUG):
                if found[slug] is None and slug_matches(url, slug):
                    found[slug] = str(git_dir)

        if all(found.values()):
            break  # stop early once both are found

    return found


def main():
    parser = argparse.ArgumentParser(description="Find priya-jobs repos on this machine.")
    parser.add_argument("--json",  action="store_true", help="Output as JSON")
    parser.add_argument("--check", action="store_true", help="Exit 1 if any repo not found")
    args = parser.parse_args()

    found = locate_repos()

    public_path  = found[PUBLIC_SLUG]
    private_path = found[PRIVATE_SLUG]

    if args.json:
        print(json.dumps({"public": public_path, "private": private_path}, indent=2))
    else:
        print(f"PUBLIC_REPO={public_path  or 'NOT FOUND'}")
        print(f"PRIVATE_REPO={private_path or 'NOT FOUND'}")

    if args.check:
        missing = [slug for slug, path in found.items() if path is None]
        if missing:
            print(f"\nERROR: Could not find: {', '.join(missing)}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
