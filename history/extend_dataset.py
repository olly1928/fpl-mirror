"""Build inspectable historical CSVs from the verified one-off source cache.

These are historical outcomes, never a collection of pre-deadline features.
Uncertain identities, source conflicts and unknown targets remain explicit.
"""
import argparse
import collections
import csv
import datetime
import gzip
import hashlib
import io
import json
import pathlib
import re
import sqlite3
import zoneinfo

POS={1:'GKP',2:'DEF',3:'MID',4:'FWD','Goalkeeper':'GKP','Defender':'DEF','Midfielder':'MID','Forward':'FWD'}
STAT=['minutes','goals_scored','assists','clean_sheets','goals_conceded','own_goals',
      'penalties_saved','penalties_missed','yellow_cards','red_cards','saves','bonus','ea_ppi','bps']
CLUBS={'ARS':'Arsenal','AVL':'Aston Villa','BOU':'Bournemouth','BUR':'Burnley','CAR':'Cardiff',
       'CHE':'Chelsea','CRY':'Crystal Palace','EVE':'Everton','FUL':'Fulham','HUL':'Hull',
       'LEI':'Leicester','LIV':'Liverpool','MCI':'Man City','MUN':'Man United','NEW':'Newcastle',
       'NOR':'Norwich','QPR':'QPR','REA':'Reading','SOU':'Southampton','STK':'Stoke','SUN':'Sunderland',
       'SWA':'Swansea','TOT':'Tottenham','WAT':'Watford','WBA':'West Brom','WHU':'West Ham','WIG':'Wigan',
       'BLA':'Blackburn','BOL':'Bolton','BIR':'Birmingham','BLP':'Blackpool','WOL':'Wolves'}
BASE=['season','gw','player_id','code','name','pos','fixture_id','kickoff_time','team_id','opponent_team',
      'was_home','value','has_xg','has_starts','has_dc','minutes','starts','total_points','goals_scored',
      'assists','clean_sheets','goals_conceded','expected_goals','expected_assists','expected_goal_involvements',
      'expected_goals_conceded','defensive_contribution','bonus','saves','yellow_cards','red_cards','own_goals',
      'penalties_missed','penalties_saved','bps','ea_ppi','source_repository','source_sha256','available_at']


def digest(body):return hashlib.sha256(body).hexdigest()


def write_rows(path,rows,columns=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    columns=columns or sorted({k for r in rows for k in r})
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=columns,extrasaction='raise')
    writer.writeheader();writer.writerows(rows)
    with path.open('wb') as f,gzip.GzipFile(fileobj=f,mode='wb',filename='',mtime=0) as g:g.write(stream.getvalue().encode())
    return {'file':path.name,'rows':len(rows),'columns':len(columns),'sha256':digest(path.read_bytes()),'bytes':path.stat().st_size}


def source_records(root):
    value=json.loads((root/'manifest.json').read_text())
    if value['failures']:raise ValueError('Incomplete source collection: '+str(root))
    return value['records']


def body(root,record):
    raw=(root/record['object']).read_bytes()
    if digest(raw)!=record['sha256']:raise ValueError('Source checksum mismatch')
    return raw


def csv_rows(raw):
    rows=[]
    try:text=raw.decode('utf-8-sig')
    except UnicodeDecodeError:text=raw.decode('cp1252')
    for r in csv.DictReader(io.StringIO(text)):
        row={k:v for k,v in r.items() if k is not None}
        if r.get(None):row['_unlabelled_extra_fields']=json.dumps(r[None])
        rows.append(row)
    return rows


def players(value):
    if isinstance(value,list):return [p for p in value if isinstance(p,dict) and 'id' in p]
    if isinstance(value,dict):
        if 'id' in value:return [value]
        return [p for p in value.values() if isinstance(p,dict) and 'id' in p]
    return []


def classic_panel(source,record,year,aux):
    roster=players(json.loads(body(source,record)));season=f'{year}-{str(year+1)[-2:]}'
    football=next(r for r in source_records(aux) if r.get('season')==season and r['repository']=='football-data.co.uk')
    fixtures=[r for r in csv_rows(body(aux,football)) if r.get('HomeTeam') and r.get('Date')]
    if len(fixtures)!=380:raise ValueError('Expected 380 verified fixture results')
    names=sorted({r[k] for r in fixtures for k in ('HomeTeam','AwayTeam')});teams={n:i+1 for i,n in enumerate(names)}
    lookup={}
    for i,f in enumerate(fixtures,1):
        date=datetime.datetime.strptime(f['Date'],'%d/%m/%y' if len(f['Date'].split('/')[-1])==2 else '%d/%m/%Y').date()
        for home in (True,False):
            key=(date,f['AwayTeam' if home else 'HomeTeam'],home)
            if key in lookup:raise ValueError('Ambiguous fixture date/opponent')
            lookup[key]=(100000+i,f)
    rows=[];seen=set();point_errors=[];used=set();ids=set();codes=set()
    for p in roster:
        if p['id'] in ids or p['code'] in codes:raise ValueError('Duplicate historical player identity')
        ids.add(p['id']);codes.add(p['code']);history=p.get('fixture_history',{}).get('all',[])
        if sum(h[-1] for h in history)!=p['total_points']:point_errors.append(p['id'])
        for h in history:
            if len(h)!=20:raise ValueError('Unexpected classic fixture schema')
            match=re.fullmatch(r'([A-Z]{3})\(([HA])\) (\d+)-(\d+)',h[2])
            if not match:raise ValueError('Unrecognised old opponent format')
            opponent,venue,own,other=match.groups();is_home=venue=='H'
            date=datetime.datetime.strptime('2000 '+h[0],'%Y %d %b %H:%M')
            date=date.replace(year=year if date.month>=7 else year+1,tzinfo=zoneinfo.ZoneInfo('Europe/London'))
            fixture_id,f=lookup[(date.date(),CLUBS[opponent],is_home)]
            if [int(own),int(other)]!=[int(f['FTHG' if is_home else 'FTAG']),int(f['FTAG' if is_home else 'FTHG'])]:raise ValueError('Fixture score disagreement')
            key=(p['id'],fixture_id)
            if key in seen:raise ValueError('Duplicate player fixture')
            seen.add(key);used.add(fixture_id)
            row={k:'' for k in BASE};row.update(season=season,gw=h[1],player_id=p['id'],code=p['code'],
                name=(p.get('first_name','')+' '+p.get('second_name','')).strip() or p['web_name'],
                pos=POS.get(p.get('element_type',p.get('element_type_id',p.get('type_name')))),
                fixture_id=fixture_id,kickoff_time=date.astimezone(datetime.timezone.utc).isoformat().replace('+00:00','Z'),
                team_id=teams[f['HomeTeam' if is_home else 'AwayTeam']],opponent_team=teams[CLUBS[opponent]],
                was_home=is_home,value=h[-2],has_xg=0,has_starts=0,has_dc=0,total_points=h[-1],
                source_repository=record['repository'],source_sha256=p.get('_source_sha256',record['sha256']))
            row.update(zip(STAT,h[3:17]))
            if row['pos'] is None or not 0<=float(row['minutes'])<=90:raise ValueError('Invalid position/minutes')
            rows.append(row)
    if point_errors:raise ValueError('Player points totals disagree: '+str(point_errors))
    return rows,{'season':season,'rows':len(rows),'players':len(roster),'fixtures':len(used),'gameweeks':sorted({int(r['gw']) for r in rows}),
                 'season_point_disagreements':0,'identity':'explicit official player code','source':record['repository'],
                 'source_sha256':record['sha256'],'fixture_sha256':football['sha256'],
                 'scope':'retrospective outcomes; historical deadline roster/news/schedule vintages unavailable'}


def build_annual(work,output):
    versions=[];issues=[]
    for r in source_records(work/'player-histories'):
        snapshot=r['season']
        for h in csv_rows(body(work/'player-histories',r)):
            season=h.get('season_name','').replace('_','/');code=h.get('element_code','')
            if not season or not code:issues.append({'path':r['path'],'reason':'missing explicit season or code'});continue
            value={k:h.get(k,'') for k in STAT+['total_points','expected_goals','expected_assists','starts','defensive_contribution']}
            for key in ('expected_goals','expected_assists','starts'):
                if int(season[:4])<2022:value[key]=''
            if int(season[:4])<2025:value['defensive_contribution']=''
            versions.append(dict(value,season=season.replace('/','-'),code=code,source_snapshot_season=snapshot,
                source_repository=r['repository'],source_sha256=r['sha256'],source_path=r['path']))
    for r in source_records(work/'early-archives'):
        if not r['path'].endswith('.json'):continue
        for p in players(json.loads(body(work/'early-archives',r))):
            for h in p.get('season_history',[]):
                if not isinstance(h,list) or len(h)!=17 or not isinstance(h[0],str):continue
                if not p.get('code'):continue
                versions.append(dict(zip(STAT,h[1:15]),season=h[0].replace('/','-').replace('_','-'),code=str(p['code']),
                    total_points=h[-1],source_snapshot_season='early-archive',source_repository=r['repository'],
                    source_sha256=r['sha256'],source_path=r['path']))
    official=work/'original/official'
    if official.exists():
        bootstrap=json.loads((official/'bootstrap.json').read_text())
        codes={p['id']:p['code'] for p in bootstrap['elements']}
        for path in sorted((official/'players').glob('*.json')):
            if path.name.endswith('.meta.json'):continue
            p=json.loads(path.read_text());meta=json.loads(path.with_suffix('.json.meta.json').read_text())
            for h in p['history_past']:
                if int(h['element_code'])!=codes[int(path.stem)]:raise ValueError('Official identity mismatch')
                value={k:h.get(k,'') for k in STAT+['total_points','expected_goals','expected_assists','starts','defensive_contribution']}
                for key in ('expected_goals','expected_assists','starts'):
                    if int(h['season_name'][:4])<2022:value[key]=''
                if int(h['season_name'][:4])<2025:value['defensive_contribution']=''
                versions.append(dict(value,season=h['season_name'].replace('/','-'),code=str(h['element_code']),
                    source_snapshot_season='2026-27',source_repository='official-FPL',source_sha256=meta['sha256'],source_path=meta['url']))
    grouped=collections.defaultdict(list)
    for r in versions:grouped[(r['season'],str(r['code']))].append(r)
    canonical=[];conflicts=[]
    for key,values in sorted(grouped.items()):
        chosen=max(values,key=lambda r:({'official-FPL':2,'vaastav/Fantasy-Premier-League':1}.get(r['source_repository'],0),r['source_snapshot_season'],r['source_path']))
        fields={f:sorted({str(v.get(f,'')) for v in values if v.get(f,'')!=''},key=str) for f in ('minutes','total_points','goals_scored','assists')}
        # Numeric formatting differences are not value disagreements.
        bad={f:vs for f,vs in fields.items() if len({float(v) for v in vs})>1}
        if bad:conflicts.append({'season':key[0],'code':key[1],'fields':bad,'chosen_source_sha256':chosen['source_sha256']})
        canonical.append(dict(chosen,source_versions=len(values),core_conflict=bool(bad),available_at=''))
    artifacts=[write_rows(output/'annual-history-versions.csv.gz',versions),write_rows(output/'annual-history.csv.gz',canonical)]
    (output/'annual-conflicts.json').write_text(json.dumps({'source_parse_issues':issues,'conflicts':conflicts},indent=2)+'\n')
    return artifacts,{'unique_player_seasons':len(canonical),'distinct_player_codes':len({r['code'] for r in canonical}),
        'rows_by_season':dict(sorted(collections.Counter(r['season'] for r in canonical).items())),'core_conflicting_keys':len(conflicts),
        'semantics':'Season aggregates; historical survivor coverage varies. Conflicting keys require reconciliation before use.'}


def build_understat(work,output):
    chosen={};versions=0;conflict=set();mapping=collections.defaultdict(set)
    root=work/'understat'
    for r in source_records(root):
        match=re.search(r'/understat/(.+)_(\d+)\.csv$',r['path'])
        if match:
            pid=match[2]
            for row in csv_rows(body(root,r)):
                if not row.get('id'):continue
                versions+=1;key=(pid,row['id']);row=dict(row,understat_player_id=pid,player_name=match[1],source_snapshot_season=r['season'],source_sha256=r['sha256'])
                if key in chosen:
                    if any(abs(float(row.get(f,0))-float(chosen[key].get(f,0)))>(1e-5 if f in ('xG','xA') else 0) for f in ('time','goals','assists','xG','xA')):conflict.add(key)
                    if row['source_snapshot_season']<chosen[key]['source_snapshot_season']:continue
                chosen[key]=row
        elif r['path'].endswith('/id_dict.csv'):
            rawplayers=work/'original/archive'/r['season']/'players_raw.csv'
            if rawplayers.exists():
                codes={str(p['id']):p['code'] for p in csv_rows(rawplayers.read_bytes())}
                for row in csv_rows(body(root,r)):
                    row={k.strip():v.strip() for k,v in row.items()}
                    if row.get('FPL_ID') in codes:mapping[row['Understat_ID']].add(codes[row['FPL_ID']])
    rows=[]
    for key,row in sorted(chosen.items()):
        codes=mapping[row['understat_player_id']];row.update(fpl_code=next(iter(codes)) if len(codes)==1 else '',crosswalk_status='explicit_map' if len(codes)==1 else 'conflict' if codes else 'unmapped',core_source_conflict=key in conflict,available_at='')
        rows.append(row)
    artifact=write_rows(output/'understat-player-matches.csv.gz',rows)
    return artifact,{'unique_player_matches':len(rows),'source_row_versions':versions,'players':len({r['understat_player_id'] for r in rows}),
                    'rows_by_start_year':dict(sorted(collections.Counter(r.get('season','') for r in rows).items())),
                    'core_conflicting_keys':len(conflict),'xg_numeric_tolerance':1e-5,'explicitly_mapped_rows':sum(bool(r['fpl_code']) for r in rows),
                    'semantics':'Understat observed appearances from multiple leagues; no FPL points or invented zero-minute rows. Unknown mapping remains blank.'}


def build_databases(work,output):
    versions=[];physical={}
    for r in source_records(work/'early-archives'):
        if r['repository']!='sjp4/differentialfpl' or not r['path'].endswith('.db3'):continue
        path=work/'early-archives'/r['object'];body(work/'early-archives',r)
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as c:
            c.execute('PRAGMA query_only=ON');c.execute('PRAGMA trusted_schema=OFF');c.row_factory=sqlite3.Row
            tables={x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'player_match','fixture','player_season','player','team'}<=tables:continue
            fixtures={x['_id']:dict(x) for x in c.execute('SELECT * FROM fixture')}
            identities={(x['season'],x['player_id']):dict(x) for x in c.execute('SELECT * FROM player_season')}
            people={x['_id']:dict(x) for x in c.execute('SELECT * FROM player')};teams={x['_id']:x['name'] for x in c.execute('SELECT * FROM team')}
            for raw in c.execute('SELECT * FROM player_match'):
                x=dict(raw);pid=x.get('player_player_id',x.get('player_id'))
                f=fixtures.get(x['fixture_id']);p=identities.get((x['season'],pid))
                if not f or not p:continue
                season=f"20{x['season']-1:02d}-{x['season']:02d}"
                kickoff=datetime.datetime.fromtimestamp(f['datetime'],datetime.timezone.utc).isoformat()
                row={'season':season,'player_id':p['fpl_id'],'database_player_id':pid,
                     'name':people.get(pid,{}).get('name',''),'gw':x['gameweek'],
                     'kickoff_time':kickoff,'home_team':teams.get(f['team_home_id'],''),'away_team':teams.get(f['team_away_id'],''),
                     'minutes':x.get('minutes'),'total_points':x.get('total'),'goals_scored':x.get('goals'),'assists':x.get('assists'),
                     'pos':p.get('position'),'source_sha256':r['sha256'],'source_path':r['path'],'available_at':''}
                versions.append(row)
    grouped=collections.defaultdict(list)
    for r in versions:grouped[(r['season'],r['database_player_id'],r['kickoff_time'],r['home_team'],r['away_team'])].append(r)
    rows=[]
    for key,values in sorted(grouped.items()):
        chosen=dict(max(values,key=lambda r:(r['total_points'] is not None,r['minutes'] is not None,r['source_path'])))
        conflicts=[]
        for f in ('minutes','total_points'):
            numbers={float(v[f]) for v in values if v[f] is not None}
            if len(numbers)>1:chosen[f]=None;conflicts.append(f)
            elif numbers:chosen[f]=next(iter(numbers))
        chosen['core_conflict']=','.join(conflicts);chosen['source_versions']=len(values)
        chosen['identity_conflict']=len({(v['player_id'],v['name'].strip().casefold()) for v in values})>1
        chosen['complete_points_and_minutes']=chosen['minutes'] is not None and chosen['total_points'] is not None and not conflicts and not chosen['identity_conflict'] and 0<=chosen['minutes']<=90
        rows.append(chosen)
    artifact=write_rows(output/'partial-database-player-matches.csv.gz',rows)
    report={'observed_and_unknown_rows':len(rows),'source_row_versions':len(versions),'seasons':{},
            'semantics':'Partial populations and seasons; NULL means unknown. Core conflicts blanked. Namespaced historical player IDs; no automatic global player join.'}
    for season in sorted({r['season'] for r in rows}):
        selected=[r for r in rows if r['season']==season]
        report['seasons'][season]={'rows':len(selected),'complete_points_and_minutes':sum(r['complete_points_and_minutes'] for r in selected),
            'unknown_minutes':sum(r['minutes'] is None for r in selected),'unknown_points':sum(r['total_points'] is None for r in selected),
            'conflicting_keys':sum(bool(r['core_conflict']) for r in selected),'identity_conflicting_keys':sum(r['identity_conflict'] for r in selected),'players':len({r['database_player_id'] for r in selected})}
    return artifact,report


def main(work,output,base_panel=None):
    output.mkdir(parents=True,exist_ok=True);records=source_records(work/'early-archives');artifacts=[];panels=[]
    for year,repo,path in [(2013,'keithxm23/fplPlayer','data.json'),(2014,'llimllib/fantasypl_stats','data/players.1435579666.json')]:
        record=next(r for r in records if r['repository']==repo and r['path']==path)
        rows,profile=classic_panel(work/'early-archives',record,year,work/'auxiliary')
        artifacts.append(write_rows(output/(f'fpl-{year}-{str(year+1)[-2:]}.csv.gz'),rows,BASE));panels.append(profile)
    # The closed 2015/16 source stores one player response per file.
    originals=[r for r in records if r['repository']=='mvbfontes/premierleaguedatasets' and r['path'].startswith('PlayersInfo/')]
    payload=[dict(json.loads(body(work/'early-archives',r)),_source_sha256=r['sha256']) for r in originals]
    temporary=work/'early-archives/objects/closed-2015.json';temporary.write_text(json.dumps(payload,separators=(',',':')))
    record={'repository':'mvbfontes/premierleaguedatasets','object':'objects/closed-2015.json','sha256':digest(temporary.read_bytes())}
    rows,profile=classic_panel(work/'early-archives',record,2015,work/'auxiliary');profile['source_manifest_sha256']=digest((work/'early-archives/manifest.json').read_bytes())
    artifacts.append(write_rows(output/'fpl-2015-16.csv.gz',rows,BASE));panels.append(profile)
    annual_files,annual=build_annual(work,output);artifacts.extend(annual_files)
    understat_file,understat=build_understat(work,output);artifacts.append(understat_file)
    annual_understat=[]
    for r in source_records(work/'understat'):
        if r['repository']=='vibedatascience/understat_players_aggregated' and r['path'].endswith('.csv'):
            annual_understat.extend(dict(h,source_sha256=r['sha256'],source_file=r['path'],complete_season=str(h['year'])!='2025') for h in csv_rows(body(work/'understat',r)))
    artifacts.append(write_rows(output/'understat-player-seasons.csv.gz',annual_understat))
    understat['auxiliary_player_seasons']=len(annual_understat)
    understat['auxiliary_partial_2025_26_rows']=sum(r['year']=='2025' for r in annual_understat)
    database_file,database=build_databases(work,output);artifacts.append(database_file)
    odds=[]
    for r in source_records(work/'auxiliary'):
        if r['repository']=='football-data.co.uk' and r['path'].endswith('.csv'):
            for row in csv_rows(body(work/'auxiliary',r)):
                if row.get('HomeTeam') and row.get('Date'):odds.append(dict(row,season=r['season'],source_sha256=r['sha256'],odds_available_at=''))
    artifacts.append(write_rows(output/'team-results-and-odds.csv.gz',odds))
    complete=None
    if base_panel:
        original=csv_rows(base_panel.read_bytes());combined=original[:]
        for season in ('2014-15','2015-16'):
            with gzip.open(output/('fpl-'+season+'.csv.gz'),'rb') as f:combined.extend(csv_rows(f.read()))
        keys=[(r['season'],str(r['player_id']),str(r['fixture_id'])) for r in combined]
        if len(keys)!=len(set(keys)):raise ValueError('Duplicate complete-panel player fixture')
        artifacts.append(write_rows(output/'fpl-complete-seasons.csv.gz',combined))
        current={str(p['code']) for p in json.loads((work/'original/official/bootstrap.json').read_text())['elements']}
        codes={str(r['code']) for r in combined};absent={c for c in codes if c not in current}
        complete={'rows':len(combined),'seasons':len({r['season'] for r in combined}),'distinct_player_codes':len(codes),
                  'absent_current_codes':len(absent),'absent_current_rows':sum(str(r['code']) in absent for r in combined),
                  'original_rows':len(original),'additional_complete_panel_rows':len(combined)-len(original),
                  'duplicate_player_fixture_keys':0,'base_panel_sha256':digest(base_panel.read_bytes())}
    result={'schema':'fpl-extended-data-v1','panels':panels,'complete_fpl_panel':complete,'annual':annual,'understat':understat,'partial_databases':database,
            'team_results':{'rows':len(odds),'seasons':len({r['season'] for r in odds}),'publication_warning':'Opening/closing odds lack exact FPL-deadline publication proof; later match stats are outcomes.'},
            'artifacts':artifacts,'production_model_changed':False,'live_feeds_changed':False}
    (output.parent/'coverage.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='artifacts'},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True)
    p.add_argument('--base-panel',type=pathlib.Path)
    a=p.parse_args();main(a.work,a.output,a.base_panel)
