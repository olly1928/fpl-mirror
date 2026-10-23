"""Normalise archived FPL outcomes without treating missing fields as zero data.

Source: Vaastav Anand's FPL Historical Dataset. This is a retrospective archive,
not a complete collection of pre-deadline snapshots. Only lagged outcomes may
be used as features; xP, status, news, selection and same-GW value are excluded.
"""
import argparse
import collections
import csv
import json
import pathlib

POS={1:'GKP',2:'DEF',3:'MID',4:'FWD'}
STATS=['minutes','starts','total_points','goals_scored','assists','clean_sheets','goals_conceded',
       'expected_goals','expected_assists','expected_goal_involvements','expected_goals_conceded',
       'defensive_contribution','bonus','saves','yellow_cards','red_cards','own_goals',
       'penalties_missed','penalties_saved']


def read(path):
    b=pathlib.Path(path).read_bytes()
    try:text=b.decode('utf-8-sig')
    except UnicodeDecodeError:text=b.decode('latin-1')
    return list(csv.DictReader(text.splitlines()))


def number(value,default=0):
    return default if value in (None,'') else float(value)


def normalise(root,output):
    profiles=[];all_rows=[]
    for folder in sorted(pathlib.Path(root).iterdir()):
        path=folder/'gws/merged_gw.csv'
        if not path.exists():continue
        rows=read(path);players={int(r['id']):r for r in read(folder/'players_raw.csv')}
        fixtures={int(r['id']):r for r in read(folder/'fixtures.csv')} if (folder/'fixtures.csv').exists() else {}
        grouped=collections.defaultdict(list)
        for index,row in enumerate(rows):
            grouped[int(row['element']),int(row['fixture'])].append((index,row))
        dedup=[];replaced_placeholders=0;duplicate_performance_conflicts=0
        for key,versions in grouped.items():
            if len(versions)>1:
                signatures={tuple(r.get(k,'') for k in STATS) for _,r in versions}
                if len(signatures)>1:duplicate_performance_conflicts+=1
                if len({r['kickoff_time'] for _,r in versions})>1:replaced_placeholders+=len(versions)-1
            # COVID postponed fixtures appear once as a cancelled March row and
            # again at the June restart. Keep the actual later fixture, once.
            index,row=max(versions,key=lambda x:(x[1].get('kickoff_time',''),x[1].get('modified',''),x[0]))
            dedup.append(row)
        unknown_ids=[];normal=[];manager_rows=0
        for row in dedup:
            pid=int(row['element']);raw=players.get(pid)
            if not raw:unknown_ids.append(pid);continue
            if int(raw['element_type'])==5:
                manager_rows+=1;continue
            if not row.get('kickoff_time'):continue
            fixture=fixtures.get(int(row['fixture']),{})
            home=str(row['was_home']).lower()=='true'
            team=fixture.get('team_h' if home else 'team_a',raw.get('team',''))
            r={'season':folder.name,'gw':int(row['GW']),'player_id':pid,'code':raw['code'],
               'name':row['name'],'pos':POS[int(raw['element_type'])],
               'fixture_id':int(row['fixture']),'kickoff_time':row['kickoff_time'],
               'team_id':str(team),'opponent_team':str(row['opponent_team']),'was_home':home,
               'value':number(row.get('value')),
               'has_xg':int('expected_goals' in row and row['expected_goals']!=''),
               'has_starts':int('starts' in row and row['starts']!=''),
               'has_dc':int('defensive_contribution' in row and row['defensive_contribution']!='')}
            r.update({k:number(row.get(k)) if k in row else '' for k in STATS})
            normal.append(r)
        normal.sort(key=lambda r:(r['kickoff_time'],r['fixture_id'],r['player_id']))
        assert len({(r['player_id'],r['fixture_id']) for r in normal})==len(normal)
        invalid=[r for r in normal if not 0<=r['minutes']<=90 or r['pos'] not in POS.values()]
        if invalid:raise ValueError(f'{folder.name}: invalid minutes/position records')
        profile={'season':folder.name,'raw_rows':len(rows),'normalised_rows':len(normal),
                 'players':len({r['player_id'] for r in normal}),'fixture_count':len({r['fixture_id'] for r in normal}),
                 'gameweeks':sorted({r['gw'] for r in normal}),
                 'duplicate_rows_removed':len(rows)-len(dedup),'postponed_placeholders_removed':replaced_placeholders,
                 'duplicate_keys_with_performance_changes':duplicate_performance_conflicts,
                 'unknown_player_ids':sorted(set(unknown_ids)),
                 'assistant_manager_rows_excluded':manager_rows,
                 'xg_available':any(r['has_xg'] for r in normal),
                 'starts_available':any(r['has_starts'] for r in normal),
                 'dc_available':any(r['has_dc'] for r in normal),
                 'fixture_metadata_available':bool(fixtures)}
        profiles.append(profile);all_rows.extend(normal)
        print(json.dumps(profile),flush=True)
    output=pathlib.Path(output);output.mkdir(parents=True,exist_ok=True)
    with (output/'normalised.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(all_rows[0]));w.writeheader();w.writerows(all_rows)
    (output/'quality.json').write_text(json.dumps(profiles,indent=2)+'\n')
    return all_rows,profiles


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=pathlib.Path,required=True)
    p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();normalise(a.root,a.output)
