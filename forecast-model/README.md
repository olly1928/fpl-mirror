# FPL forecast model v2

A portable, fixed model for **2026–27**. Run it once to fetch fresh public Fantasy Premier League data, produce forecasts for the next six gameweeks, and exit. It needs Python or a manually started GitHub Actions run—not a running Mac, private account, or background service. Weekly runs update the inputs; they do not retrain the models.

**Keep the existing advisor as the primary option.** This model is an additional research option. On seven already-exposed current-season gameweeks, its full-population RMSE was **2.25275**, against **2.15945** for the frozen advisor; conditional lineup points were **392 versus 422**. These are retrospective diagnostics, not a prospective season-long result. See [MODEL_CARD.md](MODEL_CARD.md) and the [research report](research/REPORT.md).

## Run once with Python

Keep the complete package directory, including `models/`, `data/` and `verification/`. From that directory, use Python 3.12 and the pinned dependencies:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/fpl-forecast run --output-dir runs/forecast-001
```

Use a new output directory for every run. `run` fetches the public bootstrap, fixtures and each registered player's per-fixture summary, checks the season and deadline, then applies the saved models. It needs no FPL login, entry ID, owned squad or research database. Collection uses at most four requests per second and four concurrent requests, with a ten-minute default bound. A capture of 669 players takes roughly three minutes before installation and inference costs.

The results are in `runs/forecast-001/forecast/`:

| File | Use |
| --- | --- |
| `forecasts.json` | Metadata and player forecasts by gameweek and across six gameweeks |
| `gameweek-forecasts.csv` | One row per player and gameweek |
| `six-gameweek-forecasts.csv` | Player totals across the forecast horizon |
| `fixture-forecasts.csv` | Fixture-level components and diagnostics |
| `manifest.json` | Input/model identities, output hashes and replay status |

A successful command exits with status zero and writes `complete: true`. Exact prediction replay in a separate Python process is required. Failures leave inspectable evidence; an existing non-empty output directory is never overwritten. A changed season, passed target deadline or mismatched bound file fails the run.

For a saved official-format capture, without another network request:

```bash
.venv/bin/fpl-forecast forecast --capture-dir saved-capture --output-dir runs/replay-001
```

This writes the same forecast files directly in `runs/replay-001/`. A saved capture must still meet the deadline and integrity checks. Aggregated weekly mirror statistics cannot replace per-fixture histories: double gameweeks must not be split by guesswork.

## Run on GitHub, read with Claude

The repository now contains `.github/workflows/fpl-forecast-offline.yml` and `.github/workflows/fpl-forecast-on-demand.yml` for this package at `forecast-model/`. Copies in `examples/` document the installation for reuse elsewhere. The forecast workflow is manual, with **no schedule or season-long service**. Existing collector, advisor and weekly prompt files stay unchanged.

The offline workflow runs on package-related pull requests and by manual request. **Local package validation and hosted Linux offline CI have passed.** The [hosted run](https://github.com/olly1928/fpl-mirror/actions/runs/41250558816) reported 30 tests passed and one skipped; all 18 checked output columns on 32 actual-model cases matched within the declared 1e-10 tolerance, with zero fits and zero network requests. The largest component difference was 5.55e-17; the headline prediction and approximate current total matched exactly. The reviewed PR head `feb525113f76467432b3431cb6a8db64b4ba4e67` also has green [model offline CI](https://github.com/olly1928/fpl-mirror/actions/runs/41250558816) and [existing repository tests](https://github.com/olly1928/fpl-mirror/actions/runs/41250558772).

**The first fresh hosted API capture, forecast and publication is still pending.** Offline CI uses saved inputs and does not establish official API access or successful publication. Manual dispatch requires an authenticated GitHub user with repository permission; no such hosted forecast has yet been started. `release_validation_complete` denotes package/prepublication engineering clearance. The separate `hosted_execution_validated` flag remains false until that end-to-end run is evidenced.

For an explicit forecast run, open Actions → **FPL fixed-model forecast on demand** → **Run workflow**. The runner installs Python 3.12, fetches fresh official data, predicts once, and publishes bounded CSV/JSON files under `forecast-model/latest/` using the normal repository token. It requires permission to write that branch; branch protections remain enforced. Only generated `latest/` results are committed. There is no automatic training, schedule or advisor replacement.

Publication requires an origin no older than two hours, at least five minutes before the target deadline, exact replay, the supported season, and at most 16 MiB of published plaintext. The workflow uses ordinary pushes, with up to three retries for unrelated collector commits, and stops on conflicts or package changes. It never force-pushes. Raw captures and private squad records are not published. The workflow summary supplies the resulting commit.

Claude Code can run the Python command when its environment allows official API access. Ordinary Claude chat cannot execute saved model weights by reading GitHub. It can read the generated CSV/JSON instead. Where the official API is blocked, start the manual GitHub run and give Claude the result commit. Read `forecast-model/latest/publication.json` first, then use URLs pinned to that commit, for example:

```text
https://raw.githubusercontent.com/olly1928/fpl-mirror/<commit>/forecast-model/latest/forecasts.json
```

Check the origin, target gameweek, deadline, package hash and output hashes. A `latest/` branch URL can change; a commit-pinned URL is reproducible. Old forecasts remain old even if downloaded today. See [CLAUDE.md](CLAUDE.md) for the agent-facing instructions.

## What the numbers mean

The frozen procedure is `blend_direct_football_050`: an equal blend of a direct supported-score forecast and a forecast assembled from scoring components. Twelve saved model/rolling-reference artifacts implement the complete procedure and shared rolling availability estimates. The primary seed is fixed; weekly execution never chooses a new model or seed.

The default column, `approximate_current_rule_expected_total`, adds a lagged defensive-contribution estimate to the supported score. Historical assist/bonus definitions and this defensive bridge limit its interpretation. The exact `current_rule_expected_total` is deliberately unavailable. A separate status/chance column is an operational diagnostic for the next gameweek only; later weeks retain raw forecasts. It is not a six-week injury forecast.

All future gameweeks share one capture origin. Usable historical outcomes are from earlier gameweeks and must satisfy the 48-hour availability lag. Fresh current-season summaries and fixtures replace the current-season input history each run; pre-season archive rows and model weights stay fixed. Fixture changes, transfers and new information require another explicit run. Blank and double gameweeks retain their fixture identities and counts.

Local validation confirmed exact feature and prediction agreement with the lab on 669 players and 4,014 fixture rows across six gameweeks, all CSV/JSON values, and a relocated run with both Python prediction processes guarded against lab imports and original-workspace reads. This is engineering evidence, not a superiority claim or an OS-level isolation guarantee. The reviewed candidate remains preserved separately from this release copy.
