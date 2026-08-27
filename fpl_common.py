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
  * Schema drift detection, at two levels. The expected field list for each
    endpoint is an explicit constant, because FPL renames and adds fields
    between seasons and occasionally mid-season (defensive_contribution only
    appeared in 2025/26). Field presence is only half of it though: a field FPL
    keeps sending but stops populating is invisible to a key check and lands in
    the CSV as a column of blanks or zeros indistinguishable from a real result.
    check_fields catches the first, check_values the second. A silently null
    column is far worse than a loud one.
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


def _is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _is_zero(value):
    """
    Numerically zero, including the string form.

    FPL is inconsistent about this — selected_by_percent and the ICT columns
    arrive as strings while the counters arrive as ints — so a check that only
    understood int 0 would miss half the cases it exists for. False is not zero:
    a boolean field that is False everywhere is carrying a real answer.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        try:
            return float(value) == 0
        except ValueError:
            return False
    return False


def check_values(records, fields, label):
    """
    Report fields that are present on every record but carry no information.

    check_fields above only ever looks at key presence, which leaves a blind
    spot big enough to drive a season through: a field FPL keeps sending but
    stops populating passes it silently, and the column lands in the CSV full
    of blanks or zeros looking exactly like a genuine result. That is the
    "silently null column" this module's docstring says it exists to prevent,
    and until this function existed it was the one case it could not see.

    Empty and zero are reported separately because they mean different things.
    All-empty is usually FPL having dropped a value it used to send. All-zero is
    usually a counter FPL ships but never fills in — teams[].played and friends
    have behaved that way for years, sitting at 0 all season while position next
    to them updates weekly.

    Deliberately takes an explicit field list rather than scanning everything.
    Plenty of fields are legitimately zero across the board early in a season
    (own_goals, penalties_saved), and a guard that cries wolf every hour in
    August is one nobody reads by September. Pass only the fields where "nothing
    but zeros" is genuinely diagnostic.
    """
    if not records:
        return []

    blank, zero = [], []
    for field in fields:
        seen = [r.get(field) for r in records if isinstance(r, dict) and field in r]
        if not seen:
            continue  # absent entirely — that is check_fields' job, not this one
        if all(_is_blank(v) for v in seen):
            blank.append(field)
        elif all(_is_zero(v) for v in seen):
            zero.append(field)

    warnings = []
    if blank:
        warnings.append(
            f"{label}: {len(blank)} field(s) sent by the API but empty on all "
            f"{len(records)} record(s), so the column is blank rather than "
            f"meaningful: {', '.join(blank)}"
        )
    if zero:
        warnings.append(
            f"{label}: {len(zero)} field(s) sent by the API but zero on all "
            f"{len(records)} record(s), so the column cannot be told apart from a "
            f"genuine nil: {', '.join(zero)}"
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


def fold_warnings(status, owner="build_fpl"):
    """
    Collapse the whole status map into one warnings list, plus freshness.

    Every builder is a separate process, so meta.json's warnings[] is assembled
    from build_status.json rather than in-memory state. That fold used to live
    inline in build_fpl, which was fine while build_fpl was the only thing that
    ever wrote meta.json — and stopped being fine the moment a second workflow
    (odds) started running on its own schedule. Sharing it is what lets the odds
    job refresh meta.json's freshness block without reimplementing the ordering.

    The owner's own warnings come through unprefixed because they are already
    phrased as this file's own complaints; everyone else's are tagged with the
    component that raised them, so the reader knows which builder to go and look
    at. Staleness lines come last, after the substantive warnings.
    """
    components, any_stale = staleness(status)

    warnings = list((status.get(owner) or {}).get("warnings") or [])
    for name, entry in sorted(status.items()):
        if name == owner:
            continue
        for w in entry.get("warnings") or []:
            warnings.append(f"[{name}] {w}")
    for name, comp in components.items():
        if comp["stale"]:
            warnings.append(
                f"[{name}] STALE — last ran {comp['last_run_at']}, expected every "
                f"{comp['expected_interval_minutes']} min"
            )
    return warnings, components, any_stale


def refresh_meta_components(path=None):
    """
    Rewrite only meta.json's freshness block, leaving everything else alone.

    meta.json is documented as the one place to look to answer "is any part of
    this mirror out of date?", but only build_fpl writes it, and build_fpl only
    runs in the hourly FPL workflow. The odds workflow runs on its own schedule,
    so between the two, meta.json reported an odds timestamp that was hours out
    of date — a freshly pulled odds.csv sitting next to a meta.json still
    insisting the odds were from yesterday. A consumer that trusts the documented
    freshness check, as it should, gets the wrong answer.

    So the odds job calls this after it writes: components{}, stale and
    warnings[] are recomputed from build_status.json, and nothing else is
    touched. In particular fetched_at is deliberately left alone — it belongs to
    the bootstrap fetch that built the CSVs, and the CACHE CHECK note in
    meta.json tells readers it always matches the 'fetched=' in their headers.

    Returns True if meta.json was updated. A missing or unreadable meta.json is
    not an error worth failing a build over: the next FPL run rewrites it whole.
    """
    path = pathlib.Path(path) if path else DATA / "meta.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(meta, dict):
        return False

    warnings, components, any_stale = fold_warnings(read_status())
    meta["warnings"] = warnings
    meta["components"] = components
    meta["stale"] = any_stale
    # Distinct from fetched_at on purpose: this says when the freshness block was
    # last recomputed, which after an odds-only run is later than the bootstrap
    # fetch that produced the rest of the file.
    meta["components_refreshed_at"] = now_iso()
    atomic_write_json(path, meta)
    return True
