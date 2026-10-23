# Historical data and calibration research

This area collects raw evidence and runs broader research separately from the
live advisor. The previous test was a limited17-configuration sweep, not proof
of optimal calibration. Numerical model v1.0.0 remains unchanged while this
research is evaluated.

## Raw data collection

`collect_history.py` captures the official public bootstrap and every currently
listed player's `element-summary` response. It preserves the response bytes,
capture times, URLs and SHA-256 hashes. `history_past` contains previous-season
totals for those players; it is not a historical gameweek panel. Current-player
enumeration also omits retired/departed players. Tested `season=2025-26` queries
returned current-season fixtures/bootstrap, not a switch to the old season.

For complete player/fixture panels, the collector also retrieves all ten
previous seasons from the checksum-pinned
[FPL Historical Dataset](https://github.com/vaastav/Fantasy-Premier-League),
2016/17–2025/26. These archived outcomes do not recreate historical odds/news
or pre-deadline registration/fixture-schedule vintages.

The [Historical FPL data workflow](../.github/workflows/historical-data.yml)
commits compressed raw packages into a separate `history/raw/<run-id>/`
directory and records coverage in `history/collections/latest.json`. It runs
once when `collection-request.json` changes, or manually, with no cron. Existing
`data/` ingestion and live forecasts are not modified. The raw packages retain
their exact source bytes and can be inspected with Python `tarfile` or tar.

```bash
python history/collect_history.py --output history/work/collection
```

## Broader tests

The [v2 protocol](protocol-v2.json) specifies chronological model fitting,
nested selection, learned minute-state probabilities and points recalibration.
Older/current outcomes have already been inspected: these are retrospective
research checks, not a newly untouched holdout. The seven current weeks must
not select parameters. Full transfer-policy returns remain a separate question.

The research scripts and completed results will be saved here alongside a
clear account of what improves, what does not, and what still needs prospective
evidence. No research candidate is automatically installed as the live model.
