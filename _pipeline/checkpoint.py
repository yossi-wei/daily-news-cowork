#!/usr/bin/env python3
"""
Run-state checkpoints for the daily-news-digest pipeline.

The pipeline persists intermediate artifacts to _pipeline/state/<YYYY-MM-DD>/ at
phase boundaries so a retry run can RESUME from the furthest completed phase
instead of re-running everything (Phase 1's parallel agents are ~80% of run
cost). The state dir doubles as a LIVENESS BEACON: any file recently modified
under it means a run is actively working, so the safety-net retry must back off
even if the run lock looks stale.

Layout of state/<date>/:
  context.json      Phase 0 output (build_context.py --emit-agent-prompts)
  prompts/*.md      per-agent prompt files (emitted deterministically)
  returns/*.json    per-agent return files (written by the agents themselves)
  triage.json       Phase 2 intake output (intake_candidates.py --out)
  intake-log.md     scripted run-log tables (intake_candidates.py --log-md)
  stories.json      Phase 3 assembled stories (assemble_stories.py)
  history-entries.json  Phase 4 input for append_history.py
  FAILED.json       written by `mark-failed` when a retry gives up — later
                    retries must NOT re-attempt (systematic failure, not a blip)

Commands:
  checkpoint.py status --today YYYY-MM-DD [--folder PATH]
      Print a JSON snapshot: furthest completed phase, which agent returns
      exist vs. which prompts were emitted, newest-file age in minutes
      (liveness), FAILED marker if any, and whether today's digest HTML exists.
  checkpoint.py mark-failed --today YYYY-MM-DD --phase NAME --reason "..." [--folder PATH]
      Write FAILED.json (idempotent; keeps the first failure, appends others).
  checkpoint.py clean [--keep-days 2] [--folder PATH]
      Delete state dirs whose date is older than keep-days. Never touches
      non-date-named dirs. Prints what it removed.

Exit codes: always 0 for status/clean (report, don't gate); mark-failed 0 on
write, 1 on error. Pure stdlib.
"""
import argparse, json, os, re, shutil, sys, time
from datetime import datetime, timedelta

_LEGACY_DEFAULT_FOLDER = os.path.expanduser("~/Daily news")  # portable last-resort only; real resolution is --folder > $DAILY_NEWS_FOLDER > parent of _pipeline/
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Ordered from furthest to earliest; first hit wins.
PHASE_ARTIFACTS = [
    ("stories", "stories.json"),
    ("triage", "triage.json"),
    ("agents", None),          # special-cased: >=1 file under returns/
    ("context", "context.json"),
]


def resolve_folder(arg=None):
    if arg:
        return arg
    if os.environ.get("DAILY_NEWS_FOLDER"):
        return os.environ["DAILY_NEWS_FOLDER"]
    try:
        return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    except NameError:
        return _LEGACY_DEFAULT_FOLDER


def state_root(folder):
    return os.path.join(folder, "_pipeline", "state")


def state_dir(folder, today):
    return os.path.join(state_root(folder), today)


def _newest_mtime(path):
    newest = None
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                m = os.path.getmtime(os.path.join(root, f))
            except OSError:
                continue
            if newest is None or m > newest:
                newest = m
    return newest


def _nonempty(path):
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def _list_json(path):
    # 0-byte files are truncated leftovers from `clean` (the mounted folder blocks
    # unlink, so cleanup empties files instead of deleting them) — treat as absent.
    if not os.path.isdir(path):
        return []
    return sorted(f for f in os.listdir(path)
                  if f.endswith(".json") and _nonempty(os.path.join(path, f)))


def _list_md(path):
    if not os.path.isdir(path):
        return []
    return sorted(f for f in os.listdir(path)
                  if f.endswith(".md") and _nonempty(os.path.join(path, f)))


def cmd_status(args):
    folder = resolve_folder(args.folder)
    sd = state_dir(folder, args.today)
    out = {
        "today": args.today,
        "state_dir": sd,
        "exists": os.path.isdir(sd),
        "phase": "none",
        "prompts_emitted": [],
        "agents_done": [],
        "agents_missing": [],
        "newest_age_minutes": None,
        "failed": None,
        "digest_exists": os.path.isfile(
            os.path.join(folder, f"{args.today}-news-digest.html")),
    }
    if os.path.isdir(sd):
        returns = _list_json(os.path.join(sd, "returns"))
        prompts = _list_md(os.path.join(sd, "prompts"))
        out["prompts_emitted"] = prompts
        out["agents_done"] = returns
        done_slugs = {os.path.splitext(f)[0] for f in returns}
        out["agents_missing"] = sorted(
            os.path.splitext(p)[0] for p in prompts
            if os.path.splitext(p)[0] not in done_slugs)
        for phase, artifact in PHASE_ARTIFACTS:
            if phase == "agents":
                if returns:
                    out["phase"] = "agents"
                    break
            elif _nonempty(os.path.join(sd, artifact)):
                out["phase"] = phase
                break
        newest = _newest_mtime(sd)
        if newest is not None:
            out["newest_age_minutes"] = round((time.time() - newest) / 60.0, 1)
        failed_path = os.path.join(sd, "FAILED.json")
        if _nonempty(failed_path):
            try:
                out["failed"] = json.load(open(failed_path))
            except Exception:
                out["failed"] = {"unreadable": True}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_mark_failed(args):
    folder = resolve_folder(args.folder)
    sd = state_dir(folder, args.today)
    os.makedirs(sd, exist_ok=True)
    path = os.path.join(sd, "FAILED.json")
    entry = {
        "phase": args.phase,
        "reason": args.reason,
        "marked_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        data = json.load(open(path)) if os.path.isfile(path) else {"failures": []}
    except Exception:
        data = {"failures": []}
    data.setdefault("failures", []).append(entry)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    print(json.dumps({"ok": True, "failed_marker": path,
                      "n_failures": len(data["failures"])}))
    return 0


def _empty_tree(path):
    """Fallback when the mount blocks unlink (as it does for .run.lock): truncate
    every file to 0 bytes so the dir carries no stale content. checkpoint `status`
    treats 0-byte artifacts as absent."""
    n = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                open(fp, "w").close()
                n += 1
            except OSError:
                pass
    return n


def cmd_clean(args):
    folder = resolve_folder(args.folder)
    root = state_root(folder)
    removed, emptied, kept = [], [], []
    if os.path.isdir(root):
        cutoff = datetime.now().date() - timedelta(days=args.keep_days)
        for name in sorted(os.listdir(root)):
            full = os.path.join(root, name)
            if not os.path.isdir(full) or not DATE_RE.match(name):
                continue
            try:
                d = datetime.strptime(name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if d < cutoff:
                shutil.rmtree(full, ignore_errors=True)
                if os.path.isdir(full):     # mount blocked the delete — verify, don't lie
                    _empty_tree(full)
                    emptied.append(name)
                else:
                    removed.append(name)
            else:
                kept.append(name)
    print(json.dumps({"ok": True, "removed": removed, "emptied": emptied,
                      "kept": kept, "keep_days": args.keep_days}))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status")
    p.add_argument("--today", required=True)
    p.add_argument("--folder", default=None)

    p = sub.add_parser("mark-failed")
    p.add_argument("--today", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--folder", default=None)

    p = sub.add_parser("clean")
    p.add_argument("--keep-days", type=int, default=2)
    p.add_argument("--folder", default=None)

    args = ap.parse_args()
    if not DATE_RE.match(getattr(args, "today", "0000-00-00") or "") and args.cmd != "clean":
        print(json.dumps({"error": f"--today must be YYYY-MM-DD, got {args.today!r}"}))
        sys.exit(1)
    try:
        sys.exit({"status": cmd_status, "mark-failed": cmd_mark_failed,
                  "clean": cmd_clean}[args.cmd](args))
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
