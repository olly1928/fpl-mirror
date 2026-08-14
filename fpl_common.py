#!/usr/bin/env python3
"""
fpl_common.py — the plumbing every builder in this repo shares.

Four jobs, all of them in service of "set and forget":

  * HTTP that gives up gracefully. Exponential backoff on 429/5xx, no retry on
    a 4xx that will never succeed, and a hard per-process request ceiling so a
    bug in a loop cannot turn into a ban.
  * Writes that are all-or-nothing. Build into a temp file, fsync, rename over
    the target. A crash halfway through leaves the previous good file exactly
    where it was — the consumer's first move is a staleness check, and stale
    but well-formed beats fresh but truncated.
  * Schema drift detection. The expected field list for each endpoint is an
    explicit constant. FPL renames and adds fields between seasons and
    occasionally mid-season (defensive_contribution only appeared in 2025/26),
    and a silently null column is far worse than a loud one.
  * A shared status file so meta.json can report on components written by other
    processes — whether they ran, when, and what they complained about.
"""

import hashlib
import json
import os
import pathlib
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://fantasy.premierleague.com/api"
DATA = pathlib.Path("data")
CACHE = pathlib.Path(".fpl-cache")
STATUS_PATH = DATA / "build_status.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; fpl-mirror/2.0)",
    "Accept": "application/json",
}

# A whole run of every builder is a couple of dozen calls at most. Anything
# beyond this is a loop that has got away from us, and hammering an
# unauthenticated public API is how a transient outage becomes a block.
MAX_REQUESTS = int(os.environ.get("FPL_MAX_REQUESTS", "80"))
TIMEOUT = int(os.environ.get("FPL_HTTP_TIMEOUT", "30"))
MAX_ATTEMPTS = int(os.environ.get("FPL_HTTP_ATTEMPTS", "5"))

_requests_made = 0


# ---------------------------------------------------------------- basics

def now_iso():
    """ISO 8601, UTC, seconds resolution. Every file we write carries one."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def die(msg):
    """Fail loudly. Callers must not have written anything permanent yet."""
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def clean(value):
    """
    Reduce a value to something safe to drop into a naively-joined CSV cell.

    players.csv, fdr.csv and fixtures.csv are written by string join rather than
    the csv module — that is the existing on-disk contract and it is not worth
    breaking to gain quoting. Stripping commas and newlines is what keeps that
    honest. Newer files (teams, snapshots, player_history) use the csv module
    and quote properly, so they only need this for the naive-join legacy files.
    """
    return str("" if value is None else value).replace(",", " ").replace("\n", " ").strip()


# ---------------------------------------------------------------- http

def _sleep(seconds):
    time.sleep(seconds)


def http_json(url, *, headers=None, cache_ttl=0):
    """
    GET JSON with backoff, a request ceiling, and an optional on-disk cache.

    Retries 429 and 5xx and transient socket errors with exponential backoff
    (2s, 4s, 8s, 16s plus jitter). Does not retry a 404 or a 422 — those are
    answers, not failures, and repeating them just burns goodwill.

    cache_ttl caches the response body under .fpl-cache (gitignored) for that
    many seconds. Several builders run back to back in one workflow job and all
    of them want bootstrap-static; without this they would fetch it four times
    an hour for no reason.
    """
    global _requests_made

    cache_file = None
    if cache_ttl > 0:
        CACHE.mkdir(exist_ok=True)
        cache_file = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".json")
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < cache_ttl:
                try:
                    return json.loads(cache_file.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    pass  # a corrupt cache entry is not worth failing over

    if _requests_made >= MAX_REQUESTS:
        die(f"request ceiling of {MAX_REQUESTS} reached before {url}. "
            "Something is looping — refusing to keep hitting the API.")

    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _requests_made += 1
        try:
            req = urllib.request.Request(url, headers=headers or HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                payload = json.load(r)
            if cache_file is not None:
                try:
                    cache_file.write_text(json.dumps(payload), encoding="utf-8")
                except OSError:
                    pass
            return payload
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code} {exc.reason}"
            if exc.code not in (408, 425, 429, 500, 502, 503, 504):
                raise
        except Exception as exc:  # timeouts, DNS, connection resets, bad JSON
            last = f"{type(exc).__name__}: {exc}"

        if attempt < MAX_ATTEMPTS:
            delay = (2 ** attempt) + random.uniform(0, 1)
            print(f"  retry {attempt}/{MAX_ATTEMPTS - 1} for {url} in {delay:.1f}s ({last})",
                  file=sys.stderr)
            _sleep(delay)

    raise RuntimeError(f"gave up on {url} after {MAX_ATTEMPTS} attempts — {last}")


def api(path, *, cache_ttl=0):
    """Fetch an FPL API path. Raises — callers decide what is fatal."""
    return http_json(f"{BASE}{path}", cache_ttl=cache_ttl)


def api_required(path, what, *, cache_ttl=0):
    """Fetch something the output file is useless without."""
    try:
        return api(path, cache_ttl=cache_ttl)
    except urllib.error.HTTPError as exc:
        die(f"{what} — {BASE}{path} returned HTTP {exc.code} {exc.reason}")
    except Exception as exc:
        die(f"{what} — could not read {BASE}{path}: {exc}")


def requests_made():
    return _requests_made


# ---------------------------------------------------------------- atomic writes

def atomic_write(path, text):
    """
    Write via temp file and rename, so the target is never seen half-written.

    os.replace is atomic within a filesystem, which is what lets rule 3 hold:
    if validation fails or the process dies, the previous good file is still
    the one on disk.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path, obj):
    atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def write_all(pending):
    """
    Write a batch of files only once every one of them has been built.

    Takes {path: text}. The point is ordering: nothing lands until every file
    in the run has been produced and validated, so a failure while building
    file four cannot leave files one through three updated and the rest stale.
    """
    for path, text in pending.items():
        atomic_write(path, text)


# ---------------------------------------------------------------- schema drift

def check_fields(records, expected, label, report_new=True, ignore=()):
    """
    Compare what the API actually sent against the field list we expect.

    Returns a list of human-readable warnings. Missing fields are the dangerous
    case — the column still gets written, but empty, and without this it would
    be indistinguishable from a genuine zero. New fields are reported too: they
    are how you find out FPL has shipped something worth mirroring.

    Pass report_new=False when the caller only consumes a deliberate subset of an
    endpoint — build_snapshots wants twelve of bootstrap's fifty element fields,
    and listing the other thirty-eight every hour would bury the warnings that
    matter. One caller per endpoint owns the new-field report; for elements that
    is build_fpl.

    Pass `ignore` for fields that exist, have been reviewed, and are deliberately
    not mirrored. They are subtracted from the new-field report so it stays quiet
    about what is already known and still fires the moment FPL ships something
    genuinely new. Without it the guard reports the same forty fields every hour
    and stops being read, which is the failure mode it exists to prevent.

    The union of keys across every record is used rather than the first one,
    because FPL occasionally omits null-valued keys on individual elements.
    """
    if not records:
        return [f"{label}: no records returned, cannot check schema"]

    actual = set()
    for r in records:
        if isinstance(r, dict):
            actual |= set(r.keys())

    warnings = []
    missing = sorted(set(expected) - actual)
    if missing:
        warnings.append(
            f"{label}: expected field(s) absent from the API response and written "
            f"as empty: {', '.join(missing)}"
        )
    added = sorted(actual - set(expected) - set(ignore)) if report_new else []
    if added:
        warnings.append(
            f"{label}: {len(added)} new field(s) present in the API and not mirrored: "
            f"{', '.join(added)}"
        )
    return warnings


# ---------------------------------------------------------------- status file

def read_status():
    """The shared cross-process status map. Missing or corrupt reads as empty."""
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_status(component, *, expected_interval_minutes, warnings=None, **extra):
    """
    Record that a builder ran, when, and what it complained about.

    Builders run as separate processes in the same workflow job, so meta.json
    cannot see their warnings directly. This is the hand-off: each one drops its
    result here, and build_fpl.py — which runs last — folds the lot into
    meta.json's warnings[] and stale flag. That way a partial failure surfaces
    in exactly the same place a total failure does, which is the first thing the
    downstream consumer reads.
    """
    status = read_status()
    status[component] = {
        "last_run_at": now_iso(),
        "expected_interval_minutes": expected_interval_minutes,
        "warnings": list(warnings or []),
        **extra,
    }
    atomic_write_json(STATUS_PATH, status)


def parse_iso(value):
    """Parse an ISO 8601 timestamp, tolerating a trailing Z. None if unparseable."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def staleness(status, now=None):
    """
    Turn the status map into per-component freshness plus one overall flag.

    A component is stale once it has gone longer than twice its expected refresh
    interval without a run. Twice, not once, so a single skipped or slow run does
    not cry wolf — GitHub's cron is best-effort and routinely drifts by minutes.
    """
    now = now or datetime.now(timezone.utc)
    components, any_stale = {}, False
    for name, entry in sorted(status.items()):
        last = parse_iso(entry.get("last_run_at"))
        interval = entry.get("expected_interval_minutes") or 60
        limit = timedelta(minutes=2 * interval)
        is_stale = last is None or (now - last) > limit
        components[name] = {
            "last_run_at": entry.get("last_run_at"),
            "expected_interval_minutes": interval,
            "stale_after_minutes": int(limit.total_seconds() // 60),
            "stale": is_stale,
        }
        any_stale = any_stale or is_stale
    return components, any_stale
