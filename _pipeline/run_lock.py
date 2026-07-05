#!/usr/bin/env python3
"""
run_lock.py  acquire [--stale-minutes N]   |   release

A self-expiring run lock so two pipeline runs never execute concurrently
(e.g. the early-morning scheduled run firing while a manual run is still mid-flight).

This is COMPLEMENTARY to check_existing_digest.py:
  - check_existing_digest.py prevents a *duplicate output* (a valid digest was
    already produced recently) — it can only see a run that has FINISHED.
  - run_lock.py prevents *concurrent runs* — it guards the window where run A
    has started but not yet written its HTML, during which the dup-guard is blind.

The orchestrator calls `acquire` right after the duplicate-run guard, and
`release` at the very end of the run (and on any early-abort path).

ACQUIRE exit codes:
  0   -> lock acquired, proceed with the run
  101 -> another run is active (fresh "held" lock) -> ABORT

RELEASE exit codes:
  0   -> always (releasing a missing/already-released lock is fine; idempotent)

Design notes:
  * The lock is a single JSON file at _pipeline/.run.lock with a "state" field
    ("held" or "released"), a start time, pid, and host.
  * It is NEVER deleted — the Cowork-mounted folder blocks unlink(), so release
    works by atomically overwriting the file with state "released" via a temp
    file + os.replace (an atomic rename that the mount *does* allow). This is
    why we don't use a delete-based or O_EXCL-steal lock here.
  * Liveness is decided by AGE + STATE, not by pid: each pipeline step is a
    separate short-lived process, so there is no long-lived pid to probe. A lock
    is acquirable if it is missing, already "released", or "held" but older than
    --stale-minutes (default 30, comfortably longer than a normal run). Because
    runs are daily, a stale lock always clears long before the next fire.
  * Strict cross-process create-atomicity (O_EXCL) is intentionally not used,
    since the no-delete mount makes it unusable for stealing. The remaining
    check-then-write window is immaterial here: the two runs that could ever
    collide (scheduled vs. manual) are seconds-to-minutes apart, not microsecond
    aligned, and the downstream safety nets (idempotent append_history.py,
    date-keyed deterministic build, and check_existing_digest.py) make a rare
    double-acquire degrade to redundant work, never corruption.
"""

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent / ".run.lock"
DEFAULT_STALE_MINUTES = 30


def _now():
    return time.time()


def _atomic_write(payload: dict):
    """Install lock contents via temp file + atomic rename (no unlink needed)."""
    tmp = LOCK_PATH.with_suffix(".lock.tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(str(tmp), str(LOCK_PATH))  # atomic on the mount; overwrites in place


def _read_lock():
    """Return (data_dict, age_seconds). data is None if no/unreadable lock.

    Age is measured from the recorded start time, falling back to file mtime if
    the JSON is corrupt so a bad lock can still age out.
    """
    try:
        raw = LOCK_PATH.read_text()
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, None
    try:
        data = json.loads(raw)
        started = float(data.get("started_at_epoch", 0))
    except Exception:
        try:
            started = LOCK_PATH.stat().st_mtime
        except OSError:
            return None, None
        data = {"state": "held", "corrupt": True}  # treat corrupt as held-but-ageable
    return data, _now() - started


def _held_payload():
    return {
        "state": "held",
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at_epoch": _now(),
        "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }


def acquire(stale_minutes):
    data, age = _read_lock()

    # No lock, or a released lock -> free to take.
    if data is None or data.get("state") == "released":
        _atomic_write(_held_payload())
        print(f"[lock] acquired ({LOCK_PATH.name}).")
        return 0

    # A "held" lock exists — fresh or stale?
    age_min = (age or 0) / 60
    if age is not None and age_min > stale_minutes:
        print(f"[lock] stale lock found ({age_min:.0f} min old > {stale_minutes}-min "
              f"window) — assuming crashed run, taking it over.")
        _atomic_write(_held_payload())
        print(f"[lock] acquired ({LOCK_PATH.name}).")
        return 0

    held_by = data.get("started_at_iso", "unknown time")
    print(f"[lock] ABORT: another run is active (lock {age_min:.0f} min old, "
          f"started {held_by}, within {stale_minutes}-min window).")
    return 101


def release():
    try:
        payload = {
            "state": "released",
            "released_at_epoch": _now(),
            "released_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        }
        _atomic_write(payload)
        print(f"[lock] released ({LOCK_PATH.name}).")
    except OSError as e:
        # Never fail a run because release hiccuped; the stale window covers it.
        print(f"[lock] release: could not update lock ({e}); it will age out.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Self-expiring run lock for the news pipeline.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_acq = sub.add_parser("acquire", help="Acquire the lock (exit 0 ok, 101 if another run active).")
    p_acq.add_argument("--stale-minutes", type=int, default=DEFAULT_STALE_MINUTES,
                       help=f"Age past which a held lock is considered abandoned (default {DEFAULT_STALE_MINUTES}).")

    sub.add_parser("release", help="Release the lock (always exits 0).")

    args = parser.parse_args()
    if args.cmd == "acquire":
        sys.exit(acquire(args.stale_minutes))
    else:
        sys.exit(release())


if __name__ == "__main__":
    main()
