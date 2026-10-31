# Using this fixed FPL model

This is the 2026–27 portable qualification model, version 2.0.0. Its default is `blend_direct_football_050`. Twelve saved model/rolling-reference artifacts are already present. Apply them to fresh data; do not train, tune, replace weights, change the selected recipe, install a schedule or modify the existing advisor/collector/weekly prompt.

The existing advisor remains primary. This package is an additional research option, not a proven improvement. The seven exposed current-season diagnostic weeks produced full-population RMSE 2.25275 versus 2.15945 for frozen v1, and conditional lineup points 392 versus 422. See [MODEL_CARD.md](MODEL_CARD.md) and [research/REPORT.md](research/REPORT.md).

## If Python execution and official API access are available

Follow the Python 3.12 installation in [README.md](README.md), using the exact pinned requirements. From the complete package directory:

```bash
fpl-forecast run --output-dir runs/<unique-run-name>
```

The command fetches official public inputs once, forecasts, and exits. It needs no private account, entry ID, owned squad, research lab or background Mac. It must exit zero with `forecast/manifest.json` reporting `complete: true`, zero physical fits/model updates and complete exact fresh-process replay. Do not bypass failed hash, season, deadline, dependency or replay checks.

Read `forecast/forecasts.json`, `forecast/gameweek-forecasts.csv` and `forecast/six-gameweek-forecasts.csv` under that run directory. Report the capture origin, target gameweek and deadline with any recommendation. A saved capture can be used with `fpl-forecast forecast --capture-dir <capture> --output-dir <new-directory>`; this is a replay, not a new live capture.

## If this is ordinary Claude chat or the official API is blocked

Reading the repository or a `.joblib` file does not execute the models. Do not invent forecasts from model names or archived outputs. The repository has installed manual forecast and offline-CI workflows. Hosted Linux offline CI passed, including all 18 checked columns on 32 saved actual-model cases within the declared 1e-10 tolerance; it did not fetch new official data. The first fresh hosted capture/forecast/publication remains pending because manual dispatch requires an authenticated GitHub user with repository permission. Use a completed **FPL fixed-model forecast on demand** run, or ask the user to start it. No automatic schedule, season-long service or background Mac is assumed.

Read `forecast-model/latest/publication.json` at the exact result commit. Verify `complete`, season, origin, target gameweek, deadline, package/selection hashes and the listed output hashes. Read the CSV/JSON at the same commit, not a moving branch URL. If the deadline has passed, the origin is stale, or no successful publication exists, say the forecasts are stale/unavailable and request a fresh manual run. Do not present an old capture as current.

## Interpretation

Use `approximate_current_rule_expected_total` for the packaged approximate total and retain its label. `supported_score` omits the defensive bridge. The exact `current_rule_expected_total` remains unavailable. Keep the next-gameweek status/chance diagnostic separate from the raw forecast; do not apply today's flags to all six weeks. Appearance columns aggregated over fixtures may be expected counts in a double gameweek, not a probability for a single binary event.

The same capture origin is used across six gameweeks. Future fixture changes and transfers are unknown. Do not invent a fixture-level history by dividing aggregated weekly mirror data. The package neither knows the user's current 15-player squad nor evaluates chips/transfers. Never infer personal ownership from the archived research aggregate.

Publish or cite only bounded generated outputs and public aggregate evidence. Preserve hashes and the complete models/data/verification directories. `release_validation_complete` concerns completed package/prepublication engineering checks. `hosted_execution_validated` remains false until a fresh hosted end-to-end run succeeds. Do not infer hosted API/publication success from passing offline CI. Deployment preference and evidence of superiority remain separate decisions.
