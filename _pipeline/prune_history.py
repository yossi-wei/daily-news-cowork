#!/usr/bin/env python3
"""
Maintenance helper: prune story-history.json to keep only the last N days,
archiving the rest to story-history-archive-YYYY.json (one file per calendar year,
appended if it already exists).

Designed to run weekly or monthly. Safe to run at any time:
  * Atomic writes — temp-file + os.replace on both the live file and any archive.
  * Idempotent — re-running with the same --keep-days produces no further changes.
  * Never deletes data — old entries go to the archive, never to /dev/null.
  * --dry-run reports what would happen without touching any file.

Same folder-resolution chain as the other _pipeline helpers:
  1. --folder argument
  2. $DAILY_NEWS_FOLDER environment variable
  3. parent of _pipeline/ (this script's location)
  4. legacy hardcoded macOS path (last resort)

Usage:
  prune_history.py [--folder PATH] [--keep-days 90] [--dry-run]
"""
import argparse, json, os, sys, tempfile
from datetime import datetime, timedelta

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


def atomic_write_json(path, data):
    """Write data as JSON to path via temp-file + os.replace (atomic on same filesystem)."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_archive(path):
    """Load an existing archive file, or return a fresh structure if absent/unreadable."""
    if not os.path.exists(path):
        return {"stories": []}
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception as e:
        # Don't fail the prune if the archive is corrupt — just warn and start fresh.
        print(f"WARNING: could not read existing archive {path}: {e} — starting fresh",
              file=sys.stderr)
        return {"stories": []}


def main():
    ap = argparse.ArgumentParser(
        description="Prune story-history.json, archiving entries older than --keep-days.")
    ap.add_argument("--folder", default=None,
                    help="digest folder; defaults to $DAILY_NEWS_FOLDER or parent of _pipeline/")
    ap.add_argument("--keep-days", type=int, default=90,
                    help="retain entries dated within this many days (default: 90)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would happen without writing any files")
    args = ap.parse_args()

    if args.keep_days <= 0:
        print(json.dumps({"error": "--keep-days must be a positive integer"}), file=sys.stderr)
        sys.exit(2)

    folder = resolve_folder(args.folder)
    live_path = os.path.join(folder, "story-history.json")

    # --- load and validate the live file ---
    try:
        live_data = json.load(open(live_path, encoding="utf-8"))
    except FileNotFoundError:
        print(json.dumps({"error": f"story-history.json not found at {live_path}"}),
              file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"story-history.json is not valid JSON: {e}"}),
              file=sys.stderr)
        sys.exit(2)

    stories = live_data.get("stories", [])
    today = datetime.now().date()
    cutoff = today - timedelta(days=args.keep_days)

    keep, archive_by_year = [], {}
    unparseable = []

    for s in stories:
        d = s.get("date")
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            # Keep entries with unparseable dates — don't silently drop them.
            unparseable.append(s)
            keep.append(s)
            continue

        if dt >= cutoff:
            keep.append(s)
        else:
            year = str(dt.year)
            archive_by_year.setdefault(year, []).append(s)

    total_archive = sum(len(v) for v in archive_by_year.items() if isinstance(v, list))
    # archive_by_year.items() gives (year, list) pairs — fix the count:
    total_archive = sum(len(v) for v in archive_by_year.values())

    report = {
        "dry_run": args.dry_run,
        "keep_days": args.keep_days,
        "cutoff": cutoff.isoformat(),
        "total_before": len(stories),
        "kept": len(keep),
        "archived": total_archive,
        "unparseable_kept": len(unparseable),
        "archive_files": [],
    }

    if total_archive == 0:
        report["note"] = "nothing to prune — all entries are within the keep window"
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    if not args.dry_run:
        # Write each year's archive.
        for year, entries in archive_by_year.items():
            archive_path = os.path.join(folder, f"story-history-archive-{year}.json")
            arch_data = load_archive(archive_path)
            # Idempotency: don't re-add entries already in the archive.
            existing_keys = {
                (s.get("date", ""), s.get("id") or s.get("story_id") or "")
                for s in arch_data["stories"]
            }
            added_to_arch = 0
            for s in entries:
                key = (s.get("date", ""), s.get("id") or s.get("story_id") or "")
                if key not in existing_keys:
                    arch_data["stories"].append(s)
                    existing_keys.add(key)
                    added_to_arch += 1
            atomic_write_json(archive_path, arch_data)
            report["archive_files"].append({
                "path": archive_path,
                "year": year,
                "entries_added": added_to_arch,
                "total_in_archive": len(arch_data["stories"]),
            })

        # Write the pruned live file.
        live_data["stories"] = keep
        atomic_write_json(live_path, live_data)
        report["total_after"] = len(keep)
    else:
        # Dry run: just describe what would happen.
        for year, entries in archive_by_year.items():
            archive_path = os.path.join(folder, f"story-history-archive-{year}.json")
            report["archive_files"].append({
                "path": archive_path,
                "year": year,
                "would_archive": len(entries),
            })

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
