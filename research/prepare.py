"""As-of feature construction for optional research; no production side effects."""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'history/extended/data'
STATS = ['minutes', 'total_points', 'goals_scored', 'assists', 'clean_sheets',
         'goals_conceded', 'bonus', 'saves', 'yellow_cards', 'red_cards',
         'own_goals', 'penalties_saved', 'penalties_missed', 'bps',
         'expected_goals', 'expected_assists', 'expected_goals_conceded',
         'defensive_contribution', 'starts']
RATE_STATS = [x for x in STATS if x not in ('minutes', 'starts')]
POSITIONS = ['GKP', 'DEF', 'MID', 'FWD']
ALIAS = {'Manchester City': 'Man City', 'Manchester United': 'Man United',
         'Man Utd': 'Man United', 'Sheffield Utd': 'Sheffield United',
         'Nott\'m Forest': 'Nottingham Forest', 'Nottm Forest': 'Nottingham Forest',
         'Spurs': 'Tottenham', 'Wolverhampton Wanderers': 'Wolves',
         'West Bromwich Albion': 'West Brom', 'Brighton and Hove Albion': 'Brighton',
         'Ipswich Town': 'Ipswich', 'Luton Town': 'Luton'}


def team_name(value):
    return ALIAS.get(str(value), str(value))


def read_csv(path):
    if not Path(path).exists():
        return []
    with Path(path).open(encoding='utf-8-sig') as f:
        return list(csv.DictReader(line for line in f if not line.startswith('#')))


def stamp(s):
    return datetime.fromisoformat(str(s).replace('Z', '+00:00')).timestamp()


def finite(v, default=np.nan):
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def priors(panel, annual):
    """Completed previous-season outcomes, never a target-season total."""
    result = {}
    for r in annual.to_dict('records'):
        result[(str(r['season']), int(r['code']))] = {s: finite(r.get(s)) for s in STATS}
    for (season, code), rows in panel.groupby(['season', 'code'], sort=False):
        # Partial 2013 panel cannot replace a complete annual total.
        key = (str(season), int(code))
        if str(season) == '2013-14' and key in result:
            continue
        d = {s: rows[s].sum(min_count=1) for s in STATS}
        d['opportunities'] = len(rows)
        d['p_appear'] = float((rows.minutes > 0).mean())
        d['p60'] = float((rows.minutes >= 60).mean())
        d['short_minutes'] = rows.loc[rows.minutes.between(1, 59), 'minutes'].mean()
        d['long_minutes'] = rows.loc[rows.minutes >= 60, 'minutes'].mean()
        result[key] = d
    return result


class TeamResults:
    """Historical match results only. Odds columns are deliberately not read."""
    def __init__(self, path):
        df = pd.read_csv(path, usecols=['season', 'Date', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'])
        self.records = defaultdict(list)
        for r in df.to_dict('records'):
            if pd.isna(r['Date']):
                continue
            dt = datetime.strptime(r['Date'], '%d/%m/%y' if len(r['Date'].split('/')[-1]) == 2 else '%d/%m/%Y')
            # Date-only results become usable on the next UTC calendar day.
            t = (dt.replace(tzinfo=timezone.utc) + timedelta(days=1)).timestamp()
            self.add(t, r['HomeTeam'], r['AwayTeam'], r['FTHG'], r['FTAG'])
        self.finish()

    def add(self, t, home, away, hg, ag):
        if not np.isfinite(finite(hg)) or not np.isfinite(finite(ag)):
            return
        h, a = team_name(home), team_name(away)
        self.records[h].append((t, float(hg), float(ag), 1))
        self.records[a].append((t, float(ag), float(hg), 0))

    def finish(self):
        self.arrays = {}
        for team, items in self.records.items():
            values = np.array(sorted(set(items)), dtype=float)
            self.arrays[team] = values
        self.cache = {}

    def features(self, team, cutoff):
        key = (team, cutoff)
        if key in self.cache:
            return self.cache[key]
        data = self.arrays.get(team, np.empty((0, 4)))
        end = np.searchsorted(data[:, 0], cutoff, side='left') if len(data) else 0
        past = data[:end]
        # Ancient matches supply priors only while recent team evidence is absent;
        # do not pretend a club's 1995 squad is its 2025 squad.
        past = past[past[:, 0] > cutoff - 730 * 86400] if len(past) else past
        out = {'team_history_n': float(len(past))}
        for window in (6, 12, 38):
            a = past[-window:]
            for j, field in ((1, 'gf'), (2, 'ga')):
                out[f'team_{field}_{window}'] = (a[:, j].sum() + 8 * 1.4) / (len(a) + 8)
            out[f'team_cs_{window}'] = (np.sum(a[:, 2] == 0) + 3 * .28) / (len(a) + 3)
        out['team_rest_days'] = min(60, (cutoff - past[-1, 0]) / 86400) if len(past) else 60.
        out['team_matches_14d'] = float(np.sum(past[:, 0] > cutoff - 14 * 86400))
        out['team_oldest_recent_result'] = past[-1, 0] if len(past) else 0.
        self.cache[key] = out
        return out


class Understat:
    def __init__(self, path):
        df = pd.read_csv(path, low_memory=False)
        df = df[df.fpl_code.notna() & ~df.core_source_conflict.astype(bool)].copy()
        df['usable_time'] = pd.to_datetime(df.date, utc=True).astype('int64') / 1e9 + 86400
        fields = ['usable_time', 'time', 'xG', 'xA', 'shots', 'key_passes', 'npxG', 'xGChain', 'xGBuildup']
        self.data = {int(code): g.sort_values('usable_time')[fields].to_numpy(dtype=float)
                     for code, g in df.groupby('fpl_code', sort=False)}
        self.cache = {}

    def features(self, code, cutoff):
        key = (code, cutoff)
        if key in self.cache:
            return self.cache[key]
        data = self.data.get(code, np.empty((0, 9)))
        end = np.searchsorted(data[:, 0], cutoff, side='left') if len(data) else 0
        out = {}
        for days in (365, 730):
            a = data[:end]
            a = a[a[:, 0] > cutoff - days * 86400] if len(a) else a
            mins = a[:, 1].sum()
            out[f'us_n_{days}'] = float(len(a))
            out[f'us_minutes_{days}'] = mins
            for j, name in enumerate(['xg', 'xa', 'shots', 'keypasses', 'npxg', 'xgchain', 'xgbuildup'], 2):
                out[f'us_{name}_90_{days}'] = a[:, j].sum() * 90 / mins if mins > 0 else np.nan
        out['us_latest_feature_time'] = data[end-1, 0] if end else 0.
        self.cache[key] = out
        return out


def player_features(lag, prior, pos, gw, year, cutoff):
    """Only lag/prior, never target outcomes. Explicitly missing xG stays missing."""
    n = len(lag)
    out = {f'pos_{p}': float(pos == p) for p in POSITIONS}
    out.update(gw=float(gw), dc_rules=float(year >= 2025), keeper_ten_goal=float(year >= 2025),
               years_since_2013=float(year-2013), history_n=float(n),
               prior_minutes=finite(prior.get('minutes'), 0), prior_missing=float(not prior))
    prior_mins = finite(prior.get('minutes'), 0)
    opportunities = finite(prior.get('opportunities'), 38)
    prior_app = finite(prior.get('p_appear'), min(.95, prior_mins / (38 * 75))) if prior else .5
    prior_long = finite(prior.get('p60'), min(prior_app, prior_mins / (38 * 85))) if prior else .4
    for s in STATS:
        out['prior_' + s + '_90'] = finite(prior.get(s)) * 90 / prior_mins if prior_mins > 0 else np.nan
    out['prior_minutes_per_fixture'] = prior_mins / max(opportunities, 1)
    out['prior_appear'] = prior_app
    out['prior_long'] = prior_long
    out['prior_long_minutes'] = finite(prior.get('long_minutes'), 85)
    out['prior_short_minutes'] = finite(prior.get('short_minutes'), 20)
    vals = np.array([[finite(r.get(s)) for s in STATS] for r in lag], dtype=float) if n else np.empty((0, len(STATS)))
    for w in (3, 6, 12, 38):
        a = vals[-w:]
        mins = a[:, 0] if len(a) else np.array([])
        out[f'n_{w}'] = float(len(a))
        out[f'minutes_{w}'] = (mins.sum() + 2 * out['prior_minutes_per_fixture']) / (len(a) + 2)
        out[f'appear_{w}'] = (np.sum(mins > 0) + 2 * prior_app) / (len(a) + 2)
        out[f'long_{w}'] = (np.sum(mins >= 60) + 2 * prior_long) / (len(a) + 2)
        out[f'long_minutes_{w}'] = float(mins[mins>=60].mean()) if np.any(mins>=60) else out['prior_long_minutes']
        out[f'short_minutes_{w}'] = float(mins[(mins>0)&(mins<60)].mean()) if np.any((mins>0)&(mins<60)) else out['prior_short_minutes']
        out[f'points_{w}'] = (np.nansum(a[:, 1]) + 2 * finite(prior.get('total_points'), 76) / max(opportunities, 1)) / (len(a) + 2)
        for j, s in enumerate(STATS[2:], 2):
            mask = np.isfinite(a[:, j])
            exposure = mins[mask].sum()
            out[f'{s}_90_{w}'] = np.nansum(a[:, j]) * 90 / exposure if exposure else np.nan
            out[f'{s}_exposure_{w}'] = exposure if s in ('expected_goals', 'defensive_contribution') else 0
        out[f'cs_long_{w}'] = (np.nansum(a[mins >= 60, 4] > 0) + 2 * .28) / (np.sum(mins >= 60) + 2)
    out['last_minutes'] = vals[-1, 0] if n else np.nan
    out['last_points'] = vals[-1, 1] if n else np.nan
    out['zero_streak'] = float(next((i for i, a in enumerate(vals[::-1, 0]) if a > 0), n))
    out['player_rest_days'] = min(365, (cutoff - lag[-1]['_time']) / 86400) if n else 365.
    out['lag_price'] = finite(lag[-1].get('value')) if n else np.nan
    out['latest_player_time'] = lag[-1]['_time'] + 10800 if n else 0.
    # Recent exposure-weighted rates with three different decay scales.
    for half in (4, 8, 16):
        weights = .5 ** ((gw - np.array([r['gw'] for r in lag])) / half) if n else np.array([])
        for j, s in enumerate(STATS):
            mask = np.isfinite(vals[:, j])
            m = np.sum(weights[mask] * vals[mask, 0])
            out[f'ew_{s}_90_h{half}'] = np.sum(weights[mask] * vals[mask, j]) * 90 / m if m else np.nan
        out[f'ew_exposure_h{half}'] = np.sum(weights * vals[:, 0])
    return out


def fixture_features(base, team, opp, home, cutoff, teams):
    out = dict(base)
    own = teams.features(team, cutoff)
    other = teams.features(opp, cutoff)
    out.update(own)
    out.update({'opp_' + k: v for k, v in other.items()})
    out['home'] = float(home)
    out['team_missing'] = float(not own['team_history_n'])
    out['opponent_missing'] = float(not other['team_history_n'])
    out['team_goals_proxy'] = np.clip(own['team_gf_12'] * other['team_ga_12'] / 1.4 * (1.12 if home else .88), .3, 4)
    out['opp_goals_proxy'] = np.clip(other['team_gf_12'] * own['team_ga_12'] / 1.4 * (.88 if home else 1.12), .3, 4)
    return out


def refresh_team_context(df, teams):
    """Rejoin cheap match context when reusing expensive player intermediates."""
    df=df.copy()
    for k in ('team','opponent'):
        df[k]=df[k].map(team_name)
    keys=['team','opponent','f_home','cutoff']
    contexts=[]
    for team,opp,home,cutoff in df[keys].drop_duplicates().itertuples(index=False,name=None):
        f=fixture_features({},team,opp,bool(home),cutoff,teams)
        contexts.append(dict(zip(keys,[team,opp,home,cutoff]),**{'f_'+k:v for k,v in f.items() if k!='home'}))
    contexts=pd.DataFrame(contexts)
    drop=[c for c in contexts if c.startswith('f_') and c!='f_home']
    df=df.drop(columns=drop,errors='ignore')
    return df.merge(contexts,on=keys,how='left',sort=False,validate='many_to_one')


def historical(args):
    panel = pd.concat([pd.read_csv(DATA / 'fpl-2013-14.csv.gz', low_memory=False),
                       pd.read_csv(DATA / 'fpl-complete-seasons.csv.gz', low_memory=False)], ignore_index=True)
    for s in STATS:
        panel[s] = pd.to_numeric(panel[s], errors='coerce')
    panel['_time'] = pd.to_datetime(panel.kickoff_time, utc=True).astype('int64') / 1e9
    panel['code'] = panel.code.astype(int)
    annual = pd.read_csv(DATA/'annual-history.csv.gz', low_memory=False)
    previous = priors(panel, annual)
    teams = TeamResults(DATA/'team-results-and-odds.csv.gz')
    us = Understat(DATA/'understat-player-matches.csv.gz')
    output = []
    args.output.mkdir(parents=True, exist_ok=True)
    season_paths = []
    team_maps = {}
    for season, frame in panel.groupby('season', sort=True):
        cached = args.output / f'features-{season}.pkl'
        if getattr(args,'reuse_season_cache',False) and cached.exists():
            refresh_team_context(pd.read_pickle(cached),teams).to_pickle(cached)
            season_paths.append(cached)
            continue
        path = args.source/'archive'/season/'teams.csv'
        if path.exists():
            names = {int(r['id']): team_name(r['name']) for r in read_csv(path)}
        else:
            fd = pd.read_csv(DATA/'team-results-and-odds.csv.gz', usecols=['season','HomeTeam','AwayTeam'])
            d = fd[fd.season == season]
            names = {i+1: team_name(s) for i, s in enumerate(sorted(set(d.HomeTeam) | set(d.AwayTeam)))}
        team_maps[season] = names
        past = defaultdict(list)
        year = int(season[:4]); prev = f'{year-1}-{str(year)[-2:]}'
        for gw, week in frame.groupby('gw', sort=True):
            cutoff = float(week._time.min() - 5400)
            counts = week.groupby('player_id').size().to_dict()
            cache = {}
            for r in week.to_dict('records'):
                code = int(r['code']); pid = int(r['player_id']); pos = r['pos']
                if code not in cache:
                    lag = [v for v in past[code] if v['_time']+10800 < cutoff]
                    prior = previous.get((prev, code), {})
                    f = player_features(lag, prior, pos, int(gw), year, cutoff)
                    f.update(us.features(code, cutoff))
                    active = ((np.mean([v['minutes'] for v in lag[-3:]]) >= 45) if lag else False) or (gw <= 3 and finite(prior.get('minutes'),0) >= 900)
                    cache[code] = f, lag, active
                f, lag, active = cache[code]
                h = str(r['was_home']).lower() == 'true'
                team = names[int(r['team_id'])]; opp = names[int(r['opponent_team'])]
                f = fixture_features(f, team, opp, h, cutoff, teams)
                latest = max(f.get(k,0) for k in ['latest_player_time','us_latest_feature_time','team_oldest_recent_result','opp_team_oldest_recent_result'])
                if latest >= cutoff:
                    raise AssertionError('Feature exceeds cutoff')
                # Position/rule features are known independently of match outcomes.
                target = {f'y_{s}': finite(r.get(s)) for s in STATS}
                output.append(dict(season=season, year=year, gw=int(gw), player_id=pid, code=code, pos=pos,
                                   fixture_id=int(r['fixture_id']), name=r['name'], team=team, opponent=opp,
                                   cutoff=cutoff, target_time=r['_time'], latest_feature_time=latest,
                                   active=int(active), cold=int(not lag), dgw=int(counts[pid]>1),
                                   **target, **{'f_'+k:v for k,v in f.items()}))
            # Atomic GW update: neither fixture of a DGW is visible to the other.
            for r in sorted(week.to_dict('records'), key=lambda x:x['_time']):
                past[int(r['code'])].append(r)
        season_path = args.output / f'features-{season}.pkl'
        pd.DataFrame(output).to_pickle(season_path)
        season_paths.append(season_path)
        output = []
        print(json.dumps({'prepared':season,'rows':len(frame)}), flush=True)
    result = pd.concat([pd.read_pickle(p) for p in season_paths], ignore_index=True)
    # Remove intentionally redundant constant features, retaining a fixed schema.
    drops = [c for c in result if c.startswith('f_') and c.endswith('_exposure_3') and not any(x in c for x in ['expected_goals','defensive_contribution'])]
    drops += [c for c in result if c.startswith('f_') and '_exposure_' in c and not any(x in c for x in ['expected_goals','defensive_contribution','f_ew_exposure_'])]
    # Timestamps are provenance, never predictive inputs.
    drops += ['f_latest_player_time','f_us_latest_feature_time','f_team_oldest_recent_result','f_opp_team_oldest_recent_result']
    result = result.drop(columns=list(set(drops)), errors='ignore')
    args.output.mkdir(parents=True, exist_ok=True)
    result.to_pickle(args.output/'historical.pkl')
    pd.to_pickle(previous, args.output/'prior-profiles.pkl')
    (args.output/'feature-manifest.json').write_text(json.dumps({
        'rows':len(result), 'features':[c for c in result if c.startswith('f_')],
        'sources':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                   [DATA/'fpl-complete-seasons.csv.gz',DATA/'fpl-2013-14.csv.gz',DATA/'annual-history.csv.gz',DATA/'understat-player-matches.csv.gz',DATA/'team-results-and-odds.csv.gz']},
        'per_season':result.groupby('season').size().to_dict(),
        'feature_code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'protocol_sha256':hashlib.sha256((ROOT/'research/protocol.json').read_bytes()).hexdigest(),
        'target_outcomes_not_used_as_features':True}, indent=2)+'\n')
    return result, previous, teams, us


def current(args, historical_frame, previous, us):
    official = json.loads((args.source/'official/bootstrap.json').read_text())
    codes = {int(r['id']): int(r['code']) for r in official['elements']}
    refs = json.loads((ROOT/'backtests/sources/current-core-refs.json').read_text())
    observed = {(int(r['gw']), int(r['player_id'])):r for r in read_csv(args.actuals)}
    output = []
    features = [c for c in historical_frame if c.startswith('f_')]
    for ref in refs:
        gw = int(ref['gw']); folder = args.current/f'GW{gw:02d}'
        meta = json.loads((folder/'meta.json').read_text())
        cutoff = stamp(ref['committed_at'])  # Genuine snapshot time, before deadline.
        if not cutoff < stamp(meta['next_deadline']):
            raise AssertionError('Snapshot is after deadline')
        players = {int(r['id']):r for r in read_csv(folder/'players.csv')}
        team_rows = read_csv(folder/'teams.csv')
        names = {r['short_name']:team_name(r['name']) for r in team_rows}
        fixtures = read_csv(folder/'fixtures.csv')
        teams = TeamResults(DATA/'team-results-and-odds.csv.gz')
        for f in fixtures:
            if f.get('finished','').lower() == 'true' and int(f['gw'] or 0)<gw:
                t = stamp(f['kickoff']+'T00:00:00+00:00')+86400
                teams.add(t, names[f['home']], names[f['away']], finite(f['team_h_score']), finite(f['team_a_score']))
        teams.finish()
        histories = defaultdict(list)
        dates = {str(f['fixture_id']):stamp(f['kickoff']+'T12:00:00+00:00') for f in fixtures if f.get('kickoff')}
        for r in read_csv(folder/'player_history.csv'):
            if int(r['gw']) >= gw:
                raise AssertionError('Current history includes target GW')
            pid = int(r['player_id'])
            r = {**r, **{s:finite(r.get(s)) for s in STATS}, 'gw':int(r['gw'])}
            r['_time'] = max((dates.get(i,0) for i in r['fixture_id'].split('|')),default=0)
            r['value'] = np.nan  # Current player price is a genuine snapshot instead.
            histories[pid].append(r)
        squad = json.loads((ROOT/f'backtests/fixtures/squad_gw{gw}.json').read_text())
        owned = {p['element'] for p in squad['picks']}
        for pid, p in players.items():
            if (gw,pid) not in observed:
                continue
            actual = observed[(gw,pid)]
            lag = sorted(histories[pid],key=lambda r:(r['gw'],r['_time']))
            code = codes.get(pid, -pid)
            prior = previous.get(('2025-26', code),{})
            base = player_features(lag, prior, p['pos'], gw, 2026, cutoff)
            base.update(us.features(code, cutoff))
            # Only the price and status present in this exact archived snapshot.
            base['lag_price'] = finite(p['price']) * 10
            active = ((np.mean([r['minutes'] for r in lag[-3:]]) >=45) if lag else False) or (gw<=3 and finite(prior.get('minutes'),0)>=900)
            available = 0. if p.get('st') in ('i','s','u') else finite(p.get('chance_of_playing_next_round'),100)/100
            target_fixtures = [f for f in fixtures if int(f['gw'] or 0)==gw and p['team'] in (f['home'],f['away'])]
            # All seven replay weeks have one fixture per club; assert rather than
            # duplicate aggregate actuals across two fixtures.
            if len(target_fixtures)!=1:
                raise ValueError('Current per-fixture replay requires a single scheduled fixture')
            f=target_fixtures[0]; h=p['team']==f['home']; opp=f['away'] if h else f['home']
            feat = fixture_features(base,names[p['team']],names[opp],h,cutoff,teams)
            target = {f'y_{s}':finite(actual.get(s)) for s in STATS}
            output.append(dict(season='2026-27',year=2026,gw=gw,player_id=pid,code=code,pos=p['pos'],name=p['name'],
                fixture_id=int(f['fixture_id']),team=names[p['team']],opponent=names[opp],cutoff=cutoff,
                latest_feature_time=max(base['latest_player_time'],base['us_latest_feature_time']),
                target_time=stamp(f['kickoff']+'T12:00:00Z'),active=int(active),cold=int(not lag),dgw=0,
                availability=available,owned=int(pid in owned), **target,
                **{k:feat.get(k[2:],np.nan) for k in features}))
        print(json.dumps({'current_prepared':gw,'players':len(players)}),flush=True)
    result=pd.DataFrame(output)
    result.to_pickle(args.output/'current.pkl')
    sources={}
    for p in args.current.rglob('*'):
        if p.is_file():sources[str(p.relative_to(args.current))]=hashlib.sha256(p.read_bytes()).hexdigest()
    (args.output/'current-source-manifest.json').write_text(json.dumps({
        'snapshots':refs,'files':sources,'actuals_sha256':hashlib.sha256(args.actuals.read_bytes()).hexdigest(),
        'prior_caveat':'Previous-season outcomes captured later; six frozen-v1 prior discrepancies are not copied into the research features. This is retrospective reconstruction, not a pristine archived complete feature vintage.'},indent=2)+'\n')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT/'research/work/source')
    p.add_argument('--output',type=Path,default=ROOT/'research/work/features')
    p.add_argument('--current',type=Path)
    p.add_argument('--actuals',type=Path)
    p.add_argument('--reuse-season-cache',action='store_true',help='Reuse player intermediates and refresh team context; source inputs and player formulas must be unchanged')
    a=p.parse_args()
    df,old,teams,us=historical(a)
    if a.current:
        current(a,df,old,us)
