"""Vendored frozen inference arithmetic; no training/search/DB routines."""
import numpy as np
import pandas as pd

VERSION = 'qualification-data-v1'
VALUES = ['goals', 'conceded', 'clean_sheet', 'scored', 'win']
DEFAULTS = np.array([1.35, 1.35, 0.25, 0.75, 0.375])
WINDOWS = (5, 20)

def ns(series):
    return pd.to_datetime(series, utc=True).dt.as_unit('ns').astype('int64').to_numpy()

def _side_features(history, predictions, actor, venue, lag_hours):
    names = [f'{v}_mean{w}' for w in WINDOWS for v in VALUES]
    names += [f'history_n{w}' for w in WINDOWS]
    names += [f'{v}_venue_mean10' for v in VALUES] + ['venue_history_n10', 'history_n', 'days_since']
    x = np.zeros((len(predictions), len(names)), dtype=np.float32)
    x[:, :10] = np.tile(DEFAULTS, 2)
    x[:, 12:17] = DEFAULTS
    x[:, -1] = 3650
    latest = np.full(len(predictions), np.iinfo(np.int64).min, dtype=np.int64)
    origin = ns(predictions.origin)
    lag = pd.Timedelta(hours=lag_hours).value
    groups = {k: g.sort_values(['event_time', 'fixture_id'], kind='stable') for k, g in history.groupby('club_id')}
    for club, ix in predictions.groupby(actor, sort=False).indices.items():
        g = groups.get(club)
        if g is None:
            continue
        ts = ns(g.event_time)
        available = ts + lag
        values = g[VALUES].to_numpy(float)
        sums = np.vstack([np.zeros(len(VALUES)), np.cumsum(values, axis=0)])
        ends = np.searchsorted(available, origin[ix], side='left')
        for wi, w in enumerate(WINDOWS):
            starts = np.maximum(0, ends - w)
            count = ends - starts
            x[ix, wi * 5:(wi + 1) * 5] = np.divide(sums[ends] - sums[starts], count[:, None], out=np.broadcast_to(DEFAULTS, (len(ix), 5)).copy(), where=count[:, None] > 0)
            x[ix, 10 + wi] = count
        x[ix, 18] = ends
        positive = ends > 0
        latest[ix[positive]] = available[ends[positive] - 1]
        x[ix[positive], 19] = np.minimum((origin[ix[positive]] - ts[ends[positive] - 1]) / 86400000000000.0, 3650)
        for side in (0.0, 1.0):
            jj = ix[predictions.iloc[ix][venue].to_numpy(float) == side]
            if not len(jj):
                continue
            mask = g.is_home.to_numpy(float) == side
            vt = available[mask]
            vv = values[mask]
            cs = np.vstack([np.zeros(5), np.cumsum(vv, axis=0)])
            end = np.searchsorted(vt, origin[jj], side='left')
            begin = np.maximum(0, end - 10)
            n = end - begin
            x[jj, 12:17] = np.divide(cs[end] - cs[begin], n[:, None], out=np.broadcast_to(DEFAULTS, (len(jj), 5)).copy(), where=n[:, None] > 0)
            x[jj, 17] = n
    if np.any(latest >= origin):
        raise ValueError('Team outcome entered features at or after origin')
    return (pd.DataFrame(x, columns=names), latest)

def build_context_features(team, predictions, lag_hours=48):
    """Reusable on future origins with accepted club IDs; targets are unnecessary.

    Required columns: origin, club_id, opponent_id, is_home, context_missing.
    Optional canonical_fixture_id/season/gameweek enable target-GW exclusion audit.
    Returns finite float32 context features and per-row availability evidence.
    """
    if lag_hours <= 0:
        raise ValueError('Positive outcome availability lag required')
    if team.duplicated(['fixture_id', 'club_id']).any():
        raise ValueError('Duplicate team outcome')
    if team[['goals', 'conceded']].isna().any().any() or (team[['goals', 'conceded']] < 0).any().any():
        raise ValueError('Invalid observed team scores')
    history = team.copy()
    history['event_time'] = pd.to_datetime(history.event_time, utc=True)
    history['clean_sheet'] = (history.conceded == 0).astype(float)
    history['scored'] = (history.goals > 0).astype(float)
    history['win'] = (history.goals > history.conceded).astype(float)
    pred = predictions.copy().reset_index(drop=True)
    pred['origin'] = pd.to_datetime(pred.origin, utc=True)
    pred['_opponent_home'] = 1 - pred.is_home
    if pred.origin.isna().any():
        raise ValueError('Missing prediction origin')
    known = ~pred.context_missing
    if pred.loc[known, ['club_id', 'opponent_id', 'is_home']].isna().any().any():
        raise ValueError('Known context has missing team identities')
    if {'season', 'gameweek', 'canonical_fixture_id'} <= set(pred):
        target = pred.loc[known, ['season', 'gameweek', 'origin', 'canonical_fixture_id']].drop_duplicates()
        target = target.merge(history[['fixture_id', 'event_time']].drop_duplicates(), left_on='canonical_fixture_id', right_on='fixture_id', validate='many_to_one')
        if (target.event_time + pd.Timedelta(hours=lag_hours) < target.origin).any():
            raise ValueError('Target GW outcome is available before its declared origin')
    own, a = _side_features(history, pred, 'club_id', 'is_home', lag_hours)
    opp, b = _side_features(history, pred, 'opponent_id', '_opponent_home', lag_hours)
    x = pd.concat([own.add_prefix('team_'), opp.add_prefix('opponent_')], axis=1)
    x['context_missing'] = pred.context_missing.astype(np.float32)
    x['team_no_history'] = (own.history_n == 0).astype(np.float32)
    x['opponent_no_history'] = (opp.history_n == 0).astype(np.float32)
    ordered = history.sort_values(['event_time', 'fixture_id', 'club_id'], kind='stable')
    times = ns(ordered.event_time) + pd.Timedelta(hours=lag_hours).value
    ends = np.searchsorted(times, ns(pred.origin), side='left')
    begins = np.maximum(0, ends - 380)
    cs = np.r_[0.0, np.cumsum(ordered.goals.to_numpy(float))]
    n = ends - begins
    league = np.divide(cs[ends] - cs[begins], n, out=np.full(len(pred), 1.35), where=n > 0)
    x['league_goals_mean380'] = league.astype(np.float32)
    x['matchup_goals_mean20'] = (own.goals_mean20 * opp.conceded_mean20 / np.maximum(league, 0.1)).astype(np.float32)
    x['matchup_conceded_mean20'] = (own.conceded_mean20 * opp.goals_mean20 / np.maximum(league, 0.1)).astype(np.float32)
    if not np.isfinite(x).all().all():
        raise ValueError('Nonfinite context predictors')
    league_latest = np.full(len(pred), np.iinfo(np.int64).min, dtype=np.int64)
    league_latest[ends > 0] = times[ends[ends > 0] - 1]
    if np.any(league_latest >= ns(pred.origin)):
        raise ValueError('League outcome entered features at or after origin')
    provenance = pd.DataFrame({'latest_team_label_available_ns': a, 'latest_opponent_label_available_ns': b, 'latest_league_label_available_ns': league_latest})
    return (x.astype(np.float32), provenance)
