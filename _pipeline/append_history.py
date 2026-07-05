#!/usr/bin/env python3
"""
Phase 5 helper: idempotent, atomic append to story-history.json.

Reads new story entries (JSON array) from a file or stdin and appends only those
not already present. Idempotency key = (date, normalized id). This makes manual
same-day re-runs safe (the digest has had same-day double runs before).

Writes via temp-file + os.replace so a crash mid-write can never corrupt the
~240 KB history file. Validates that the existing file parses before touching it.

Usage:
  append_history.py --new new_entries.json [--folder PATH] [--dry-run]
  cat new_entries.json | append_history.py
"""
import argparse, json, os, sys, tempfile

# Folder resolution is dynamic so the helper is portable. Precedence:
#   1. --folder argument  2. $DAILY_NEWS_FOLDER  3. parent of _pipeline/ (script
#   location). The hardcoded macOS path remains only as a last-resort fallback.
_LEGACY_DEFAULT_FOLDER = os.path.expanduser("~/Daily news")  # portable last-resort only; real resolution is --folder > $DAILY_NEWS_FOLDER > parent of _pipeline/

def resolve_folder(arg=None):
    if arg:
        return arg
    env = os.environ.get("DAILY_NEWS_FOLDER")
    if env:
        return env
    try:
        return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    except NameError:
        return _LEGACY_DEFAULT_FOLDER

def norm_id(entry):
    return entry.get("id") or entry.get("story_id") or \
        f"{entry.get('date','?')}::{(entry.get('headline','') or '')[:60]}"

def key(entry):
    return (entry.get("date", ""), norm_id(entry))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=None,
                    help="digest folder; defaults to $DAILY_NEWS_FOLDER or the parent of _pipeline/")
    ap.add_argument("--new", default=None, help="JSON file with array of new entries; omit to read stdin")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    folder = resolve_folder(args.folder)

    raw = open(args.new).read() if args.new else sys.stdin.read()
    new_entries = json.loads(raw)
    if isinstance(new_entries, dict) and "stories" in new_entries:
        new_entries = new_entries["stories"]
    if not isinstance(new_entries, list):
        print("ERROR: new entries must be a JSON array", file=sys.stderr)
        sys.exit(2)

    path = os.path.join(folder, "story-history.json")
    with open(path) as f:
        data = json.load(f)              # validates existing file parses
    stories = data.get("stories", [])
    existing = {key(s) for s in stories}

    added, skipped = [], []
    for e in new_entries:
        if key(e) in existing:
            skipped.append(norm_id(e))
        else:
            stories.append(e)
            existing.add(key(e))
            added.append(norm_id(e))
    data["stories"] = stories

    report = {"added": added, "added_count": len(added),
              "skipped_duplicates": skipped, "total_after": len(stories)}

    if not args.dry_run and added:
        d = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)        # atomic on same filesystem
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
