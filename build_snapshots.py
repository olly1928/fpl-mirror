#!/usr/bin/env python3
"""
build_snapshots.py — the only genuinely irreplaceable file in this mirror.

Everything else here can be rebuilt from the API at any later date: cumulative
stats, per-gameweek splits, fixtures, results. Price, ownership, transfer counts
and injury news cannot. FPL overwrites them in place, so once a value changes
the previous one does not exist anywhere. Every hour this job does not run is an
hour permanently missing from the record.

Output: data/snapshots/YYYY-MM.csv, rolled monthly so no single file grows
without bound and every one of them stays small enough to curl.

    snapshot_at, player_id, now_cost, cost_change_event, cost_change_start,
    selected_by_percent, transfers_in_event, transfers_out_event,
    status, chance_of_playing_next_round, news, news_added

Write modes
    delta (default)  A full baseline row for every player on the first snapshot
                     of each UTC day, then only rows for players whose tracked
                     values actually moved. Lossless — forward-fill from the most
                     recent earlier row to reconstruct any player at any instant.
    full             One row per player per snapshot, exactly as specified.

Why delta is the default: at 587 players and hourly snapshots, full mode is
roughly 440,000 rows and 26 MB a month. The consumer of this mirror is Claude
fetching files over curl, and a 26 MB CSV is not usefully fetchable. Delta mode
carries identical information in about 2 MB, and the daily baseline means the
file is self-anchoring — you never have to read the previous month to resolve a
player. Pass --mode full if you want the literal one-row-per-player-per-snapshot
panel and can live with the size.

Idempotency and safety
    Rows are only ever appended; nothing already written is altered. The file is
    rebuilt into a temp path and renamed over the target, so an interrupted run
    cannot truncate a month of history. A run that reaches the API but finds
    implausibly few players exits non-zero rather than writing a thin file.
"""

import argparse
import csv
import io
import sys
from datetime import datetime, timezone

from fpl_common import (
    DATA, api_required, atomic_write, check_fields, die, now_iso,
    read_status, record_status,
)

SNAP_DIR = DATA / "snapshots"

COLS = [
    "snapshot_at", "player_id", "now_cost", "cost_change_event", "cost_change_start",
    "selected_by_percent", "transfers_in_event", "transfers_out_event",
    "status", "chance_of_playing_next_round", "news", "news_added",
]
# Everything except the timestamp and the player id. A row is only worth writing
# in delta mode when one of these has moved.
TRACKED = COLS[2:]

EXPECTED_SNAPSHOT_FIELDS = ["id"] + TRACKED

# A real bootstrap has ~600 elements. Anything near zero means the response
# shape moved or we got an error page, and writing it would quietly poison the
# one file that cannot be rebuilt.
MIN_PLAUSIBLE_PLAYERS = 100


def header_block(month, mode, rows):
    """
    The comment block, rebuilt on every append.

    Only the header moves — every data row below it is carried through byte for
    byte. Refreshing it is what keeps the file's own fetched timestamp honest
    without touching history.
    """
    return [
        f"# fpl-mirror snapshots — month={month} mode={mode} rows={rows} fetched={now_iso()}",
        "# The ephemeral fields FPL overwrites in place. Append-only; rows are never edited.",
        ("# delta mode: a full baseline for every player on the first snapshot of each UTC "
         "day, then only changed players. Forward-fill from the most recent earlier row."
         if mode == "delta" else
         "# full mode: one row per player per snapshot."),
        "# now_cost and cost_change_* are in tenths of a million, as the API reports them.",
        ",".join(COLS),
    ]


def read_existing(path):
    """
    Return (data rows as raw text lines, last known tracked values per player).

    Comment lines and the column header are dropped — they get rebuilt on write.
    Data lines are returned verbatim so an append never reformats history. Rows
    with the wrong field count are still preserved, just ignored for the purposes
    of change detection.
    """
    if not path.exists():
        return [], {}

    lines = [ln for ln in path.read_text(encoding="utf-8").split("\n")
             if ln and not ln.startswith("#")]
    if lines and lines[0].startswith("snapshot_at"):
        lines = lines[1:]

    last = {}
    for row in csv.reader(io.StringIO("\n".join(lines))):
        if len(row) == len(COLS):
            last[row[1]] = tuple(row[2:])
    return lines, last


def tracked_values(element):
    return tuple(
        "" if element.get(f) is None else str(element.get(f))
        for f in TRACKED
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--mode", choices=("delta", "full"), default="delta",
                    help="delta (default) writes changed players plus a daily baseline; "
                         "full writes every player every snapshot")
    ap.add_argument("--allow-empty", action="store_true",
                    help="do not fail when a baseline snapshot produces no rows "
                         "(for tests only)")
    args = ap.parse_args()

    boot = api_required("/bootstrap-static/", "bootstrap-static", cache_ttl=600)
    elements = boot.get("elements") or []
    if len(elements) < MIN_PLAUSIBLE_PLAYERS:
        die(f"bootstrap-static returned {len(elements)} elements, expected at least "
            f"{MIN_PLAUSIBLE_PLAYERS}. Refusing to append a thin snapshot to a file "
            "that cannot be reconstructed.")

    # Only the missing half matters here: this job reads a deliberate subset of
    # bootstrap's element fields, and build_fpl already owns the new-field report.
    warnings = check_fields(elements, EXPECTED_SNAPSHOT_FIELDS, "snapshots.elements",
                            report_new=False)

    snapshot_at = now_iso()
    today = datetime.now(timezone.utc).date().isoformat()
    month = today[:7]
    path = SNAP_DIR / f"{month}.csv"

    body, last = read_existing(path)
    status = read_status().get("build_snapshots", {})

    # A new month starts a new file, so it needs a baseline regardless of when
    # the last one was written.
    baseline = (args.mode == "full" or not body
                or status.get("last_baseline_date") != today)

    rows = []
    for e in elements:
        pid = str(e.get("id"))
        values = tracked_values(e)
        if baseline or last.get(pid) != values:
            rows.append([snapshot_at, pid] + list(values))

    if baseline and not rows and not args.allow_empty:
        die("a baseline snapshot produced no rows. A job that succeeds while writing "
            "nothing is the exact silent failure this check exists to catch.")

    if rows:
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        body += buf.getvalue().rstrip("\n").split("\n")
        atomic_write(path, "\n".join(header_block(month, args.mode, len(body)) + body) + "\n")

    record_status(
        "build_snapshots",
        expected_interval_minutes=60,
        warnings=warnings,
        last_snapshot_at=snapshot_at,
        last_baseline_date=today if baseline else status.get("last_baseline_date"),
        last_rows_written=len(rows),
        current_file=str(path),
        mode=args.mode,
    )

    kind = "baseline" if baseline else "delta"
    print(f"OK — {kind} snapshot at {snapshot_at}: {len(rows)} row(s) appended to {path}")
    if not rows:
        print("  nothing changed since the previous snapshot — expected between price runs")
    for w in warnings:
        print(f"  ! {w}", file=sys.stderr)


if __name__ == "__main__":
    main()
