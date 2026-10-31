"""Vendored frozen inference arithmetic; no training/search/DB routines."""
import numpy as np
import pandas as pd

VERSION = 'qualification-models-v1'
SEED = 20261028
FOCUS = ('known_subtotal', 'assists', 'bonus', 'minute_state', 'minutes', 'goals', 'supported_score', 'historical_points')
ASSEMBLY = ('clean_sheet_points', 'save_blocks', 'conceded_blocks', 'negative_cost', 'penalties_saved')
TARGETS = FOCUS + ASSEMBLY
KINDS = {t: 'categorical' if t == 'minute_state' else 'signed' if t in {'known_subtotal', 'supported_score', 'historical_points'} else 'continuous' if t == 'minutes' else 'count' for t in TARGETS}
POPULATIONS = {t: 'GKP' if t in {'save_blocks', 'penalties_saved'} else 'GKP_DEF' if t == 'conceded_blocks' else 'all' for t in TARGETS}
POSITIONS = ('GKP', 'DEF', 'MID', 'FWD')

def _values(panel, name):
    if name not in panel:
        return np.full(len(panel), np.nan)
    return panel[name].to_numpy(dtype=float, na_value=np.nan)

def target_arrays(panel):
    """Return original-grain targets with explicit validity and populations."""
    pos = panel.fpl_position.to_numpy()
    known_pos = np.isin(pos, POSITIONS)
    minutes = _values(panel, 'minutes')
    goals = _values(panel, 'goals_scored')
    assists = _values(panel, 'assists')
    bonus = _values(panel, 'bonus')
    cs = _values(panel, 'clean_sheets')
    saves = _values(panel, 'saves')
    conceded = _values(panel, 'goals_conceded')
    saved = _values(panel, 'penalties_saved')
    red = _values(panel, 'red_cards')
    yellow = _values(panel, 'yellow_cards')
    own = _values(panel, 'own_goals')
    missed = _values(panel, 'penalties_missed')
    negative = np.where(red > 0, 3 * red, yellow) + 2 * own + 2 * missed
    negative[~(np.isfinite(red) & np.isfinite(yellow) & np.isfinite(own) & np.isfinite(missed))] = np.nan
    state = np.where(np.isfinite(minutes), np.where(minutes == 0, 0, np.where(minutes < 60, 1, 2)), np.nan)
    goal_weight = pd.Series(pos).map({'GKP': 10, 'DEF': 6, 'MID': 5, 'FWD': 4}).to_numpy(float)
    cs_weight = pd.Series(pos).map({'GKP': 4, 'DEF': 4, 'MID': 1, 'FWD': 0}).to_numpy(float)
    known = (minutes > 0) + (minutes >= 60).astype(float) + goal_weight * goals + cs_weight * cs + saves // 3 + 5 * saved - np.isin(pos, ['GKP', 'DEF']) * (conceded // 2) - negative
    known[~np.isfinite(minutes)] = np.nan
    if 'known_score_subtotal' in panel:
        known = _values(panel, 'known_score_subtotal')
    values = {'known_subtotal': known, 'assists': assists, 'bonus': bonus, 'minute_state': state, 'minutes': minutes, 'goals': goals, 'supported_score': known + 3 * assists + bonus, 'historical_points': _values(panel, 'total_points'), 'clean_sheet_points': cs_weight * cs, 'save_blocks': saves // 3, 'conceded_blocks': conceded // 2, 'negative_cost': negative, 'penalties_saved': saved}
    result = {}
    for target, y in values.items():
        mask = known_pos & np.isfinite(y)
        if KINDS[target] in {'count', 'continuous', 'categorical'}:
            mask &= y >= 0
        if target in {'minute_state', 'minutes'}:
            mask &= np.isfinite(minutes) & (minutes >= 0) & (minutes <= 90)
        if target == 'bonus':
            mask &= y <= 3
        if target == 'clean_sheet_points':
            mask &= (cs >= 0) & (cs <= 1)
        if POPULATIONS[target] == 'GKP':
            mask &= pos == 'GKP'
        elif POPULATIONS[target] == 'GKP_DEF':
            mask &= np.isin(pos, ['GKP', 'DEF'])
        y = np.asarray(y, dtype=float)
        y[~mask] = np.nan
        result[target] = {'y': y, 'mask': mask, 'kind': KINDS[target], 'population': POPULATIONS[target]}
    return result

def rolling_columns():
    columns = []
    for target in TARGETS:
        for w in (6, 20):
            columns += [f'q_{target}_p{i}_mean{w}' for i in range(3)] if target == 'minute_state' else [f'q_{target}_mean{w}']
            columns += [f'q_{target}_observed{w}']
    return columns

def build_rolling_features(history, origins, lag_hours=48):
    """Exact target lag features for past or future origins; no fitted parameters."""
    if history.observation_id.duplicated().any():
        raise ValueError('Duplicate historical observation')
    if lag_hours <= 0:
        raise ValueError('Positive availability lag required')
    history = history.copy()
    history['event_time'] = pd.to_datetime(history.event_time, utc=True).dt.as_unit('ns')
    history = history.sort_values(['event_time', 'observation_id'], kind='stable').reset_index(drop=True)
    targets = target_arrays(history)
    cols = rolling_columns()
    x = np.zeros((len(origins), len(cols)), dtype=np.float32)
    origin = pd.to_datetime(origins.origin, utc=True).dt.as_unit('ns').astype('int64').to_numpy()
    if (origin == np.iinfo(np.int64).min).any():
        raise ValueError('Missing origin')
    latest = np.full(len(origins), np.iinfo(np.int64).min, dtype=np.int64)
    groups = {p: ii for p, ii in history.groupby('player_id', sort=False).indices.items()}
    for player, ii in origins.groupby('player_id', sort=False).indices.items():
        ix = groups.get(player)
        if ix is None:
            continue
        g = history.iloc[ix]
        ts = g.event_time.astype('int64').to_numpy()
        available = ts + pd.Timedelta(hours=lag_hours).value
        ends = np.searchsorted(available, origin[ii], side='left')
        positive = ends > 0
        latest[ii[positive]] = available[ends[positive] - 1]
        if {'season', 'gameweek'} <= set(origins):
            season = g.season.to_numpy()
            gw = g.gameweek.to_numpy()
            for end, s, w in zip(ends, origins.iloc[ii].season, origins.iloc[ii].gameweek):
                if np.any((season[:end] == s) & (gw[:end] == w)):
                    raise ValueError('Target GW entered exact rolling history')
        offset = 0
        for target in TARGETS:
            y = targets[target]['y'][ix]
            ok = targets[target]['mask'][ix]
            val = np.column_stack([(y == k) & ok for k in range(3)]).astype(float) if target == 'minute_state' else np.where(ok, y, 0)[:, None]
            sums = np.vstack([np.zeros(val.shape[1]), np.cumsum(val, axis=0)])
            counts = np.r_[0, np.cumsum(ok)]
            for w in (6, 20):
                begin = np.maximum(0, ends - w)
                n = counts[ends] - counts[begin]
                mean = np.divide(sums[ends] - sums[begin], n[:, None], out=np.zeros((len(ii), val.shape[1])), where=n[:, None] > 0)
                x[ii, offset:offset + val.shape[1]] = mean
                offset += val.shape[1]
                x[ii, offset] = n
                offset += 1
    if np.any(latest >= origin) or not np.isfinite(x).all():
        raise ValueError('Invalid rolling feature clock or values')
    return (pd.DataFrame(x, columns=cols), latest)

def _array(x):
    a = np.asarray(x, dtype=np.float64)
    if a.ndim != 2 or not np.isfinite(a).all():
        raise ValueError('Features must be a finite two-dimensional matrix')
    return a

def _normalize(pred, target, positions):
    p = np.asarray(pred, dtype=float)
    if not np.isfinite(p).all():
        raise ValueError('Nonfinite model prediction')
    if target == 'minute_state':
        if p.shape != (len(positions), 3):
            raise ValueError('Three ordered state probabilities required')
        p = np.maximum(p, 1e-09)
        return p / p.sum(axis=1)[:, None]
    p = p.reshape(-1)
    if len(p) != len(positions):
        raise ValueError('Prediction length mismatch')
    if KINDS[target] != 'signed':
        p = np.maximum(p, 0.0)
    if target == 'minutes':
        p = np.minimum(p, 90.0)
    elif target == 'bonus':
        p = np.minimum(p, 3.0)
    elif target == 'clean_sheet_points':
        p = np.minimum(p, pd.Series(positions).map({'GKP': 4.0, 'DEF': 4.0, 'MID': 1.0, 'FWD': 0.0}).to_numpy())
    return p

def predict_model(model, X_test, positions_test):
    """Apply the saved preprocessing, class map and preregistered support."""
    target = model['component']
    family = model['family']
    x = _array(X_test)
    pos = np.asarray(positions_test)
    if len(pos) != len(x) or x.shape[1] != model['feature_count'] or (not np.isin(pos, POSITIONS).all()):
        raise ValueError('Prediction population/features invalid')
    if model['feature_columns'] is not None and hasattr(X_test, 'columns') and (list(X_test.columns) != model['feature_columns']):
        raise ValueError('Prediction feature names/order changed')
    if family == 'constant':
        pred = np.eye(3)[np.full(len(pos), int(model['constant']))] if target == 'minute_state' else np.full(len(pos), model['constant'])
    elif family in {'prior', 'rolling'}:
        pred = np.array([model['by_position'].get(p, model['global_prior']) for p in pos])
        if family == 'rolling':
            cols = model['rolling_indices']
            n = x[:, cols[-1]]
            if np.any(n < 0):
                raise ValueError('Negative rolling evidence count')
            mean = x[:, cols[:-1]] if target == 'minute_state' else x[:, cols[0]]
            den = n[:, None] if target == 'minute_state' else n
            pred = (den * mean + 6 * pred) / (den + 6)
    elif family == 'portable_hist':
        from . import model as original
        pred = original.predict_model(model['estimator'], np.asarray(X_test, dtype=np.float32), pd.DataFrame({'fpl_position': pos}), model['original_component'])
    else:
        import joblib
        from threadpoolctl import threadpool_limits
        with joblib.parallel_backend('sequential'), threadpool_limits(limits=1):
            if target == 'minute_state':
                raw = model['estimator'].predict_proba(x)
                pred = np.zeros((len(x), 3))
                pred[:, model['state_classes']] = raw
            elif family == 'catboost' and KINDS[target] == 'count':
                pred = model['estimator'].predict(x, prediction_type='Exponent')
            else:
                pred = model['estimator'].predict(x)
    if model['recipe'].get('reference_role') == 'portable' and KINDS[target] == 'count':
        pred = np.maximum(pred, 1e-09)
    return _normalize(pred, target, pos)
