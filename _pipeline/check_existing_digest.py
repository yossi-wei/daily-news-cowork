#!/usr/bin/env python3
"""
check_existing_digest.py  --today YYYY-MM-DD [--window-minutes N]

Exits 100 if today's digest already exists, is valid, and was produced
within --window-minutes (default 90) before this run.
Exits 0 otherwise (proceed with full pipeline run).

The orchestrator should call this at the very start of every run. If it
exits 100, present the existing digest and stop — do not re-run the pipeline.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", required=True, help="YYYY-MM-DD")
    parser.add_argument("--window-minutes", type=int, default=90,
                        help="How many minutes old a valid digest must be to trigger abort (default 90)")
    args = parser.parse_args()

    folder = Path(__file__).resolve().parent.parent  # …/Daily news/
    html_path = folder / f"{args.today}-news-digest.html"

    if not html_path.exists():
        print(f"[check] No digest found for {args.today} — proceed with full run.")
        sys.exit(0)

    # Check age relative to now
    age_seconds = time.time() - html_path.stat().st_mtime
    age_minutes = age_seconds / 60

    if age_minutes > args.window_minutes:
        print(f"[check] Digest exists but is {age_minutes:.0f} min old "
              f"(> {args.window_minutes}-min window) — proceed with full run.")
        sys.exit(0)

    # File is recent enough — now validate it
    validate_script = Path(__file__).parent / "validate_digest.py"
    if not validate_script.exists():
        print(f"[check] validate_digest.py not found — cannot confirm validity, proceeding with full run.")
        sys.exit(0)

    result = subprocess.run(
        [sys.executable, str(validate_script), "--html", str(html_path), "--date", args.today],
        capture_output=True, text=True
    )

    try:
        validation = json.loads(result.stdout)
    except Exception:
        print(f"[check] Validation parse failed — proceeding with full run.\n"
              f"  stdout: {result.stdout!r}\n  stderr: {result.stderr!r}")
        sys.exit(0)

    if not validation.get("ok"):
        problems = validation.get("problems", [])
        print(f"[check] Digest exists but failed validation: {problems} — proceeding with full run.")
        sys.exit(0)

    # All checks pass → abort duplicate run
    notes = validation.get("notes", [])
    print(f"[check] ABORT: valid digest already exists ({html_path.name}), "
          f"produced {age_minutes:.0f} min ago (within {args.window_minutes}-min window).")
    if notes:
        print(f"[check]   Validation notes: {'; '.join(notes)}")
    print(f"[check] EXISTING_DIGEST_PATH={html_path}")
    sys.exit(100)


if __name__ == "__main__":
    main()
