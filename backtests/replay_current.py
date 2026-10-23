"""Strict pre-deadline numerical replay; advice is conditional on the actual squad.

The archived Claude advice/news overrides are unavailable. Nothing is saved to
decisions/, and replay outputs are never presented as prospective forecasts.
"""
import argparse
import copy
import csv
import importlib.util
import json
import math
import pathlib
import shutil
import statistics
import sys
import tempfile

ROOT=pathlib.Path(__file__).resolve().parent.parent


def load_model(path):
    spec=importlib.util.spec_from_file_location('replay_model',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def metrics(rows):
    if not rows:return {'n':0}
    errors=[r['actual_points']-r['xp'] for r in rows]
    return {'n':len(rows),'mean_xp':statistics.mean(r['xp'] for r in rows),
            'mean_points':statistics.mean(r['actual_points'] for r in rows),
            'bias_actual_minus_forecast':statistics.mean(errors),
            'mae':statistics.mean(abs(e) for e in errors),
            'rmse':math.sqrt(statistics.mean(e*e for e in errors)),
            'appearance_brier':statistics.mean((r['p_appear']-r['appeared'])**2 for r in rows),
            'p60_brier':statistics.mean((r['p60']-r['reached60'])**2 for r in rows)}


def team_score(model,team,players,actual):
    xi=team['xi'];bench=team['bench'];active=[i for i in xi if actual[i]['minutes']>0]
    if actual[xi[0]]['minutes']==0 and actual[bench[0]]['minutes']>0:active.append(bench[0])
    original=tuple(sum(players[str(i)]['pos']==p for i in xi[1:]) for p in ('DEF','MID','FWD'))
    missing=tuple(sum(players[str(i)]['pos']==p and actual[i]['minutes']==0 for i in xi[1:]) for p in ('DEF','MID','FWD'))
    mask=sum(1<<j for j,i in enumerate(bench[1:]) if actual[i]['minutes']>0)
    submask=model.bench_subset(original,missing,tuple(players[str(i)]['pos'] for i in bench[1:]),mask)
    active += [i for j,i in enumerate(bench[1:]) if submask & (1<<j)]
    cap=team['captain'] if actual[team['captain']]['minutes']>0 else team['vice_captain']
    score=sum(actual[i]['points'] for i in active)
    if actual[cap]['minutes']>0:score+=actual[cap]['points']
    return score


def untouched_score(model,squad_root,actuals,forecasts):
    """Transparent hold control: fixed GW1 XI, bench order and captain/vice."""
    first=model.read_json(squad_root/'squad_gw1.json')
    picks=sorted(first['picks'],key=lambda p:p['slot'])
    team={'xi':[p['element'] for p in picks[:11]],'bench':[p['element'] for p in picks[11:]],
          'captain':next(p['element'] for p in picks if p['is_captain']),
          'vice_captain':next(p['element'] for p in picks if p['is_vice_captain'])}
    weeks=[]
    for gw,forecast in sorted(forecasts.items(),key=lambda x:int(x[0])):
        outcomes={p['element']:actuals[int(gw),p['element']] for p in picks}
        weeks.append({'gw':int(gw),'points':team_score(model,team,forecast['players'],outcomes)})
    return {'definition':'Hold GW1 owned15, starting XI, bench order and captain/vice unchanged; legal actual autosubs; no transfers/hits/chips.',
            'weeks':weeks,'points':sum(w['points'] for w in weeks)}


def run(data_root,refs,actuals_path,squad_root,model_path,model_dir,output,pool_from=None):
    model=load_model(model_path)
    actuals={(int(r['gw']),int(r['player_id'])):{'minutes':float(r['minutes'] or 0),'points':float(r['total_points'] or 0)}
             for r in model.read_csv(actuals_path)}
    weekly=[];predictions=[];all_forecasts={}
    config=model.read_json(model_dir/'config.json')
    frozen_pool={(int(r['gw']),int(r['player_id'])) for r in model.read_csv(pool_from)
                 if int(r['likely_player'])} if pool_from else None
    for ref in refs:
        gw=ref['gw'];raw=data_root/f'GW{gw:02d}';meta=model.read_json(raw/'meta.json')
        clock=model.utc(ref['committed_at']);deadline=model.utc(meta['next_deadline'])
        assert clock<deadline and meta['next_event']==gw
        legacy=[]
        with tempfile.TemporaryDirectory() as temp:
            prepared=pathlib.Path(temp)
            for p in raw.iterdir():
                if p.is_file():shutil.copyfile(p,prepared/p.name)
            # GW1 warned about new price-change columns, which are neither used
            # as inputs nor needed to score a one-GW numerical forecast. Preserve
            # that warning in results; never silently disable an integrity gate.
            remaining=[]
            for warning in meta.get('warnings',[]):
                if gw==1 and 'new field' in warning and 'price_change_' in warning:
                    legacy.append(warning)
                else:remaining.append(warning)
            adapted=copy.deepcopy(meta);adapted['warnings']=remaining
            model.write_json(prepared/'meta.json',adapted)
            model.validate_data(prepared,config,clock)
            forecast=model.forecast(prepared,model_dir,{},1,clock,True,ref['sha'])
        squad=model.read_json(squad_root/(f'squad_gw{gw}.json'))
        assert squad['picks_event']==gw
        ids=[p['element'] for p in squad['picks']]
        model.legal_squad(ids,forecast['players'])
        outcomes={pid:actuals[gw,pid] for pid in ids}
        lineup=model.best_lineup(ids,forecast,gw)
        score=team_score(model,lineup,forecast['players'],outcomes)
        actual_score=sum(actuals[gw,p['element']]['points']*p['multiplier'] for p in squad['picks'])
        captain=next(p['element'] for p in squad['picks'] if p['is_captain'])
        record={'gw':gw,'snapshot_sha':ref['sha'],'snapshot_time':ref['committed_at'],
                'hours_before_deadline':(deadline-clock).total_seconds()/3600,'legacy_warnings':legacy,
                'model_version':model.VERSION,'conditional_squad_score':score,'actual_score':actual_score,
                'team_xp':lineup['xp'],'captain':forecast['players'][str(lineup['captain'])]['name'],
                'captain_base_points':outcomes[lineup['captain']]['points'],
                'haaland_base_points':actuals[gw,411]['points'],
                'actual_captain_base_points':actuals[gw,captain]['points'],
                'xi':lineup['xi'],'bench':lineup['bench']}
        weekrows=[]
        for pid,p in forecast['players'].items():
            if (gw,int(pid)) not in actuals:continue
            predicted=p['weeks'][str(gw)];observed=actuals[gw,int(pid)]
            row={'gw':gw,'player_id':int(pid),'name':p['name'],'pos':p['pos'],'xp':predicted['xp'],
                 'expected_minutes':predicted['expected_minutes'],'p_appear':predicted['p_appear'],'p60':predicted['p60'],
                 'actual_points':observed['points'],'actual_minutes':observed['minutes'],
                 'appeared':int(observed['minutes']>0),'reached60':int(observed['minutes']>=60),
                 'owned':int(int(pid) in ids),'likely_player':int((gw,int(pid)) in frozen_pool)
                 if frozen_pool is not None else int(predicted['expected_minutes']>=45)}
            row.update({k:predicted['components'].get(k,0) for k in ['appearance','goals','assists','clean_sheet','defensive_contribution','bonus','saves','conceded','discipline','penalties']})
            predictions.append(row);weekrows.append(row)
        record['all_players']=metrics(weekrows)
        record['likely_players']=metrics([r for r in weekrows if r['likely_player']])
        record['owned_players']=metrics([r for r in weekrows if r['owned']])
        weekly.append(record);all_forecasts[str(gw)]=forecast
        print(json.dumps({'gw':gw,'score':score,'actual':actual_score,'captain':record['captain'],
                          'mae_likely':record['likely_players']['mae']}),flush=True)
    result={'model_version':model.VERSION,'replay':True,'weeks':weekly,
            'research_variant':config.get('research_variant'),
            'candidate_pool':'Frozen from supplied baseline predictions' if pool_from else 'Baseline expected minutes >=45',
            'all_players':metrics(predictions),'likely_players':metrics([r for r in predictions if r['likely_player']]),
            'owned_players':metrics([r for r in predictions if r['owned']]),
            'conditional_squad_score':sum(w['conditional_squad_score'] for w in weekly),
            'actual_score':sum(w['actual_score'] for w in weekly),
            'limitations':['Conditional on actual owned squads; not a transfer-policy backtest.',
                           'No historical Claude news overrides are available; only pre-deadline feed flags are used.',
                           'Model design post-dates these seven outcomes, even though features are strictly time-bounded.',
                           'GW1 numerical replay acknowledges irrelevant new-price-column schema warnings.']}
    result['untouched_gw1_control']=untouched_score(model,squad_root,actuals,all_forecasts)
    output.mkdir(parents=True,exist_ok=True)
    model.write_json(output/'summary.json',result)
    with (output/'player_predictions.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(predictions[0]));w.writeheader();w.writerows(predictions)
    model.write_json(output/'forecasts.replay.json',all_forecasts)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-root',type=pathlib.Path,required=True)
    p.add_argument('--refs',type=pathlib.Path,required=True)
    p.add_argument('--actuals',type=pathlib.Path,required=True)
    p.add_argument('--squads',type=pathlib.Path,required=True)
    p.add_argument('--model',type=pathlib.Path,default=ROOT/'backtests/baseline-v1/fpl_advisor.py')
    p.add_argument('--model-dir',type=pathlib.Path,default=ROOT/'backtests/baseline-v1/model')
    p.add_argument('--output',type=pathlib.Path,required=True)
    p.add_argument('--pool-from',type=pathlib.Path,help='Freeze likely-player pool from baseline CSV')
    a=p.parse_args();run(a.data_root,json.loads(a.refs.read_text()),a.actuals,a.squads,a.model,a.model_dir,a.output,a.pool_from)
