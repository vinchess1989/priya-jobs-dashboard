#!/usr/bin/env python3
"""Read/write per-job tailoring metadata (apply_url, apply_email, tailor_model, ...)
in the Firestore shared_state/job_status doc, keyed by job URL.

jobs.json is scraper-owned (rewritten wholesale every ~12 min by scraper.py) and
must never be hand-patched by tailoring/apply-link skills -- doing so is what
causes recurring merge conflicts between the scraper PC and the tailoring PC.
This module is the single, canonical place any skill or script should go
through to persist or read that kind of metadata instead.

CLI usage:
    python job_status_store.py get --url "<job_url>" [--field apply_url]
    python job_status_store.py set --url "<job_url>" --field apply_url --value "https://..."
    python job_status_store.py set --url "<job_url>" --field action_item --json --value-file path.json

Note: PowerShell 5.1 silently strips embedded double-quote characters from
arguments passed to a native exe, so a JSON string passed via --value never
survives the command line intact -- use --value-file (with --json) instead
for any nested/dict value.
"""

import argparse
import json
import sys

import requests

PROJECT_ID = "priya-jobs-dashboard"
FIRESTORE_BASE = (
    f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}"
    f"/databases/(default)/documents"
)
DOC_URL = f"{FIRESTORE_BASE}/shared_state/job_status"


def _deserialize_value(v):
    if "stringValue" in v:
        return v["stringValue"]
    if "booleanValue" in v:
        return v["booleanValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "nullValue" in v:
        return None
    if "mapValue" in v:
        return {k: _deserialize_value(fv) for k, fv in v["mapValue"].get("fields", {}).items()}
    if "arrayValue" in v:
        return [_deserialize_value(av) for av in v["arrayValue"].get("values", [])]
    return None


def _deserialize_doc(doc):
    return {k: _deserialize_value(v) for k, v in doc.get("fields", {}).items()}


def _serialize_value(val):
    if val is None:
        return {"nullValue": None}
    if isinstance(val, bool):
        return {"booleanValue": val}
    if isinstance(val, int):
        return {"integerValue": str(val)}
    if isinstance(val, float):
        return {"doubleValue": val}
    if isinstance(val, str):
        return {"stringValue": val}
    if isinstance(val, dict):
        return {"mapValue": {"fields": {k: _serialize_value(v) for k, v in val.items()}}}
    if isinstance(val, list):
        return {"arrayValue": {"values": [_serialize_value(v) for v in val]}}
    return {"stringValue": str(val)}


def _serialize_doc(data):
    return {"fields": {k: _serialize_value(v) for k, v in data.items()}}


def get_job_status() -> dict:
    """Fetch the full shared_state/job_status document, keyed by job URL."""
    resp = requests.get(DOC_URL, timeout=30)
    resp.raise_for_status()
    return _deserialize_doc(resp.json())


def patch_job_status(data: dict) -> None:
    """Overwrite the full shared_state/job_status document. Callers should
    read-modify-write via get_job_status() first so sibling jobs' entries
    aren't clobbered."""
    resp = requests.patch(DOC_URL, json=_serialize_doc(data), timeout=30)
    resp.raise_for_status()


def get_job_field(job_url: str, field: str | None = None):
    """Return one field of a job's entry, or the whole entry if field is None.
    Returns None if the job/field isn't present yet."""
    current = get_job_status()
    entry = current.get(job_url)
    if entry is None:
        return None
    if field is None:
        return entry
    return entry.get(field)


def set_job_field(job_url: str, field: str, value) -> None:
    """Read-modify-write a single field on a single job's entry, preserving
    every other job's entry and every other field on this one untouched."""
    current = get_job_status()
    entry = current.get(job_url, {})
    entry[field] = value
    current[job_url] = entry
    patch_job_status(current)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_get = sub.add_parser("get", help="Read a job's field(s)")
    p_get.add_argument("--url", required=True)
    p_get.add_argument("--field", default=None)

    p_set = sub.add_parser("set", help="Write a single field on a job")
    p_set.add_argument("--url", required=True)
    p_set.add_argument("--field", required=True)
    value_group = p_set.add_mutually_exclusive_group(required=True)
    value_group.add_argument("--value", help="Plain string value")
    value_group.add_argument("--value-file",
                              help="Path to a file whose contents become the value "
                                   "(use with --json for nested values like action_item -- "
                                   "PowerShell 5.1 silently strips embedded double-quote "
                                   "characters from inline --value arguments passed to a "
                                   "native exe, so JSON must come from a file, not argv)")
    p_set.add_argument("--json", action="store_true",
                        help="Parse the value (from --value or --value-file) as JSON instead "
                             "of a plain string (needed for nested values like action_item)")

    args = parser.parse_args()

    try:
        if args.command == "get":
            result = get_job_field(args.url, args.field)
            if result is None:
                print("NONE")
            elif isinstance(result, (dict, list)):
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(result)
        elif args.command == "set":
            raw = args.value
            if args.value_file is not None:
                # utf-8-sig: PowerShell 5.1's Set-Content -Encoding utf8 always writes a
                # BOM (no utf8NoBOM option exists pre-Core), which plain utf-8 chokes on.
                with open(args.value_file, "r", encoding="utf-8-sig") as f:
                    raw = f.read()
            value = json.loads(raw) if args.json else raw
            set_job_field(args.url, args.field, value)
            print(f"OK  set {args.field!r} on {args.url}")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
