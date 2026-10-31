"""Fresh official capture to fixed qualification features; no database or fitting."""
import numpy as np
import pandas as pd

from . import model
from .context_features import build_context_features
from .qualification_models import build_rolling_features
from .inputs import read_capture,summary_history
from .history import read_history
from .utils import bound_file,read_json,utc,POSITIONS


def validate_clubs(bootstrap,club_map,season):
    if season!='2026-27' or club_map['season']!=season:raise ValueError('Fixed team map supports2026-27 only')
    expected={int(t['id']):(int(t['code']),t['canonical_club_id'])for t in club_map['teams']}
    actual={int(t['id']):int(t['code'])for t in bootstrap['teams']}
    if len(expected)!=20 or len(actual)!=20 or len(bootstrap['teams'])!=20 or len(set(v[1]for v in expected.values()))!=20:
        raise ValueError('Twenty unique evidenced clubs required')
    if actual!={k:v[0]for k,v in expected.items()}:raise ValueError('Official team ID/code map drifted from frozen season evidence')
    return {k:v[1]for k,v in expected.items()}


def current_team_history(fixtures,clubs,season,origin,target_gameweek):
    origin=utc(origin);rows=[];seen=set();excluded={'unfinished':0,'target_or_future_gameweek':0,'availability_lag':0}
    for f in fixtures:
        fid=int(f['id'])
        if fid in seen:raise ValueError('Duplicate official team fixture')
        seen.add(fid)
        home,away=int(f['team_h']),int(f['team_a'])
        if home not in clubs or away not in clubs or home==away:raise ValueError('Fixture has unknown or repeated club')
        if not f.get('finished'):excluded['unfinished']+=1;continue
        if f.get('event') is None or int(f['event'])>=int(target_gameweek):excluded['target_or_future_gameweek']+=1;continue
        event=utc(f['kickoff_time'])
        if event+pd.Timedelta(hours=48)>=origin:excluded['availability_lag']+=1;continue
        scores=[f.get('team_h_score'),f.get('team_a_score')]
        if any(v is None or not np.isfinite(float(v)) or float(v)<0 or float(v)!=int(v)for v in scores):
            raise ValueError('Completed eligible fixture has missing/invalid team scores')
        for own,opponent,goals,conceded,is_home in [(home,away,*scores,1.),(away,home,scores[1],scores[0],0.)]:
            rows.append({'fixture_id':f'current-fpl:{season}:{fid}','season':season,'event_time':event,
                'club_id':clubs[own],'opponent_id':clubs[opponent],'goals':int(goals),'conceded':int(conceded),'is_home':is_home})
    return pd.DataFrame(rows,columns=['fixture_id','season','event_time','club_id','opponent_id','goals','conceded','is_home']),excluded


def future_cases(bootstrap,fixtures,clubs,season,origin,weeks):
    origin=utc(origin);teams={int(t['id']):t for t in bootstrap['teams']};rows=[];players=[]
    for p in bootstrap['elements']:
        team=int(p['team'])
        if team not in clubs:raise ValueError('Player club absent from fixed club map')
        info={'fpl_element_id':int(p['id']),'fpl_code':str(p['code']),'player_id':'fpl-code:'+str(p['code']),
            'name':p['web_name'],'team':teams[team]['short_name'],'fpl_position':POSITIONS[int(p['element_type'])],
            'price_million_gbp':float(p['now_cost'])/10.,'snapshot_status':p.get('status'),
            'chance_of_playing_next_round':p.get('chance_of_playing_next_round'),'can_select':p.get('can_select')}
        players.append(info)
        for f in fixtures:
            if f.get('event')not in weeks or team not in [int(f['team_h']),int(f['team_a'])]:continue
            event=utc(f['kickoff_time']) if f.get('kickoff_time')else pd.NaT
            if f.get('started')or f.get('finished')or(pd.notna(event)and event<=origin):raise ValueError('Requested forecast fixture has already started')
            home=team==int(f['team_h']);opponent=int(f['team_a']if home else f['team_h']);fid=int(f['id'])
            if opponent not in clubs:raise ValueError('Opponent absent from fixed club map')
            rows.append({**info,'observation_id':f'qualification-forecast:{season}:{p["code"]}:{fid}',
                'season':season,'gameweek':int(f['event']),'origin':origin,'event_time':event,
                'fpl_fixture_id':fid,'canonical_fixture_id':f'current-fpl:{season}:{fid}',
                'club_id':clubs[team],'opponent_id':clubs[opponent],'context_missing':False,
                'was_home':home,'is_home':float(home)})
    cases=pd.DataFrame(rows)
    if cases.empty:raise ValueError('No forecast fixtures in supplied horizon')
    if cases.observation_id.duplicated().any():raise ValueError('Duplicate future case')
    return cases.sort_values(['fpl_element_id','gameweek','fpl_fixture_id']).reset_index(drop=True),pd.DataFrame(players)


def prepare(package_root,capture_dir,manifest):
    capture,bootstrap,fixtures,summaries,weeks=read_capture(capture_dir)
    if capture['season']!=manifest['supported_season']:raise ValueError('Capture and model seasons differ')
    first=min(bootstrap['events'],key=lambda e:int(e['id']))
    if utc(first['deadline_time']).year!=int(manifest['supported_season'][:4]):raise ValueError('Official season calendar drifted')
    origin=utc(capture['origin_utc'])
    if utc(capture['target_deadline_utc'])<=pd.Timestamp.now(tz='UTC'):raise ValueError('Captured target deadline has passed')
    assets=manifest['assets']
    def asset(name):return bound_file(package_root,assets[name]['path'],assets[name]['sha256'])
    club_map=read_json(asset('club-map.json'));clubs=validate_clubs(bootstrap,club_map,capture['season'])
    ref=assets['player-history-index.json'];archive=read_history(package_root,ref['path'],ref['sha256'])
    old_teams=pd.read_parquet(asset('team-history.parquet'))
    if not (archive.season<capture['season']).all()or not(old_teams.season<capture['season']).all():
        raise ValueError('Packaged history contains current/future season; cannot append capture safely')
    current,player_exclusions=summary_history(bootstrap,fixtures,summaries,capture['season'],origin,weeks[0])
    new_teams,team_exclusions=current_team_history(fixtures,clubs,capture['season'],origin,weeks[0])
    history=pd.concat([archive,current],ignore_index=True,sort=False);team_history=pd.concat([old_teams,new_teams],ignore_index=True,sort=False)
    team_history['event_time']=pd.to_datetime(team_history.event_time,utc=True)
    if history.observation_id.duplicated().any()or team_history.duplicated(['fixture_id','club_id']).any():raise ValueError('Historical append multiplied identities')
    cases,players=future_cases(bootstrap,fixtures,clubs,capture['season'],origin,weeks)
    base=model.build_features(history,cases);context,evidence=build_context_features(team_history,cases,48)
    rolling,latest=build_rolling_features(history,cases,48)
    feature_sets={'base':base,'opponent_enhanced':pd.concat([base,context],axis=1),'rolling_exact':pd.concat([base,rolling],axis=1)}
    if any(not np.isfinite(x).all().all()for x in feature_sets.values()):raise ValueError('Nonfinite current features')
    if (evidence.to_numpy()>=origin.value).any()or(latest>=origin.value).any():raise ValueError('Outcome entered frozen current origin')
    defensive=model.defensive_prior(history,cases)
    return {'capture':capture,'cases':cases,'players':players,'weeks':weeks,'feature_sets':feature_sets,
        'defensive_prior':defensive,'context_evidence':evidence,'rolling_latest_available_ns':latest,
        'current_player_rows':len(current),'current_team_rows':len(new_teams),'player_exclusions':player_exclusions,
        'team_exclusions':team_exclusions,'clubs':clubs,'physical_fits':0}
